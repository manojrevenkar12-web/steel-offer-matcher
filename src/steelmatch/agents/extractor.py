"""RFQ extraction: the model reads and quotes, code decides every value from the quotes.

The model returns, for each field, a value and the exact text it read it from. Code then
- drops any quote that is not in the document (a model that invents text gets nothing),
- reads numbers from the quote itself, not from the model's number,
- resolves the grade through the knowledge base (identifiers decide, names propose),
- accepts a policy flag only when the quote says it in words a trader would use,
- and lists what is missing or unclear as review items for a person.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.usage import UsageLimits

from ..docs.reader import DocumentText
from ..domain.dates import delivery_by
from ..domain.grades import GradeKB
from ..domain.spec import LineSpec, Policy
from ..domain.units import read_field

ROOT = Path(__file__).resolve().parents[3]


# What the model returns ------------------------------------------------------------------
class AltClaim(BaseModel):
    kind: Literal["thickness_mm", "width_min_mm"]
    value: float | None = None
    quote: str = ""


class LineClaim(BaseModel):
    line: int = 1
    form: Literal["coil", "sheet", "plate", "strip"] | None = None
    form_quote: str = ""
    finish: Literal["hot_rolled", "cold_rolled", "coated", "stainless"] | None = None
    finish_quote: str = ""
    grade: str | None = None
    grade_quote: str = ""
    coating: str | None = None
    coating_quote: str = ""
    surface: str | None = None
    surface_quote: str = ""
    thickness_mm: float | None = None
    thickness_quote: str = ""
    width_mm: float | None = None
    width_quote: str = ""
    length_mm: float | None = None
    length_quote: str = ""
    quantity_t: float | None = None
    quantity_quote: str = ""
    max_price_eur_t: float | None = None
    price_quote: str = ""
    alternatives: list[AltClaim] = Field(default_factory=list)


class PolicyClaim(BaseModel):
    upgrades_ok: bool = False
    upgrades_quote: str = ""
    secondary_ok: bool = False
    secondary_quote: str = ""
    certificate: Literal["none", "2.2", "3.1", "3.2"] = "none"
    certificate_quote: str = ""
    excluded_origins: list[str] = Field(default_factory=list)
    origins_quote: str = ""


class RfqClaims(BaseModel):
    is_rfq: bool
    language: Literal["de", "en", "other"] = "de"
    lines: list[LineClaim] = Field(default_factory=list)
    policy: PolicyClaim = Field(default_factory=PolicyClaim)
    delivery_country: str | None = None
    delivery_place_quote: str = ""
    delivery_quote: str = ""
    questions: list[str] = Field(default_factory=list)


# What code decides ------------------------------------------------------------------------
@dataclass
class ReviewItem:
    line: int
    field: str
    reason: str
    proposals: tuple[str, ...] = ()


@dataclass
class Extraction:
    status: Literal["ready", "review", "not_rfq"]
    lines: list[LineSpec]
    policy: Policy
    review: list[ReviewItem] = field(default_factory=list)
    evidence: dict[str, dict[str, str]] = field(default_factory=dict)  # "line.field" -> {quote, source}
    invalid_quotes: int = 0
    unresolved_grades: list[str] = field(default_factory=list)
    language: str = "de"


_FORM_WORDS = {
    "coil": r"coil|ring|rolle|\bC\b",
    "sheet": r"blech|tafel|sheet|formatblech|\d\s*[x×]\s*\d+\s*[x×]\s*\d",
    "plate": r"grobblech|plate|platte",
    "strip": r"spaltband|band|strip|slit",
}
_FINISH_WORDS = {
    "hot_rolled": r"warmgewalzt|warmband|hot[\s-]?rolled|\bHR\b|\bWW\b",
    "cold_rolled": r"kaltgewalzt|kaltband|cold[\s-]?rolled|\bCR\b|\bKW\b",
    "coated": r"verzinkt|feuerverzinkt|galvani[sz]ed|\bHDG\b|zinc|\+?Z\d{2,3}|\+?AZ\d{2,3}|\+?ZM\d{2,3}",
    "stainless": r"edelstahl|rostfrei|stainless|inox|1\.4\d{3}|V2A|V4A",
}
_FAMILY_FINISH = {"structural": "hot_rolled", "hr_forming": "hot_rolled", "cr_forming": "cold_rolled",
                  "coated_forming": "coated", "coated_structural": "coated", "stainless_austenitic": "stainless",
                  "stainless_ferritic": "stainless"}
_POLICY_WORDS = {
    "upgrades": r"höher\w*\s+güte|höherwertig|or better|oder besser|oder höher|higher grade|upgrade|auch\s+S\d{3}|gleichwertig oder besser",
    "secondary": r"2\.\s?wahl|ii\.\s?wahl|zweite wahl|second[\s-]choice|secondary|b-ware|2nd choice",
}


@lru_cache(maxsize=1)
def _places() -> dict:
    return json.loads((ROOT / "knowledge" / "places.json").read_text())


def decide(claims: RfqClaims, doc: DocumentText, kb: GradeKB, required: tuple[str, ...], today: date,
           transcribed_sources: set[str] | None = None) -> Extraction:
    transcribed_sources = transcribed_sources or set()
    if not claims.is_rfq:
        return Extraction("not_rfq", [], Policy())
    review: list[ReviewItem] = []
    evidence: dict[str, dict[str, str]] = {}
    invalid = 0
    unresolved: list[str] = []

    def quoted(key: str, quote: str) -> str | None:
        nonlocal invalid
        if not quote or not quote.strip():
            return None
        if not doc.contains(quote):
            invalid += 1
            return None
        src = doc.source_of(quote) or "email"
        evidence[key] = {"quote": quote.strip(), "source": src}
        return src

    # Policy -----------------------------------------------------------------------------
    p = claims.policy
    policy = Policy()
    if p.upgrades_ok and quoted("policy.upgrades", p.upgrades_quote) and re.search(_POLICY_WORDS["upgrades"], p.upgrades_quote, re.I):
        policy.upgrades_ok = True
    if p.secondary_ok and quoted("policy.secondary", p.secondary_quote) and re.search(_POLICY_WORDS["secondary"], p.secondary_quote, re.I):
        policy.secondary_ok = True
    if p.certificate != "none":
        if quoted("policy.certificate", p.certificate_quote) and p.certificate in p.certificate_quote:
            policy.certificate = p.certificate
        else:
            review.append(ReviewItem(0, "certificate", f"certificate {p.certificate} claimed without a matching quote"))
    if p.excluded_origins and quoted("policy.origins", p.origins_quote):
        names = {c: words for c, words in _places()["countries"].items()}
        extra = {"CN": ["China", "chinesisch", "Chinese"], "IN": ["Indien", "India", "indisch"], "TR": ["Türkei", "Turkey", "türkisch"],
                 "RU": ["Russland", "Russia", "russisch"], "UA": ["Ukraine"], "KR": ["Korea"], "VN": ["Vietnam"]}
        names.update(extra)
        regions = {"ASIA": ["Asien", "Asia", "asiatisch", "Asian"]}
        asian = ["CN", "IN", "KR", "VN", "TW", "JP", "TH", "ID", "MY"]
        if any(re.search(rf"\b{w}", p.origins_quote, re.I) for w in regions["ASIA"]):
            policy.excluded_origins.extend(asian)
        for code in p.excluded_origins:
            words = names.get(code.upper(), [code])
            if code.upper() not in policy.excluded_origins and any(re.search(rf"\b{re.escape(w)}", p.origins_quote, re.I) for w in words):
                policy.excluded_origins.append(code.upper())

    # Delivery ---------------------------------------------------------------------------
    country = "DE"
    if claims.delivery_country:
        src = quoted("delivery.place", claims.delivery_place_quote)
        place = claims.delivery_place_quote
        ok = False
        if src:
            pl = _places()
            code = claims.delivery_country.upper()
            words = pl["countries"].get(code, []) + [c for c, cc in pl["cities"].items() if cc == code]
            ok = any(re.search(rf"\b{re.escape(w)}\b", place, re.I) for w in words)
        if ok:
            country = claims.delivery_country.upper()
        elif claims.delivery_country.upper() != "DE":
            review.append(ReviewItem(0, "delivery_country", "delivery country not confirmed by the email; assumed Germany"))
    by: date | None = None
    if claims.delivery_quote:
        if quoted("delivery.date", claims.delivery_quote):
            by, understood = delivery_by(claims.delivery_quote, today)
            if not understood:
                review.append(ReviewItem(0, "delivery_by", f"cannot read a date from {claims.delivery_quote!r}"))

    # Lines ------------------------------------------------------------------------------
    lines: list[LineSpec] = []
    for i, lc in enumerate(claims.lines, start=1):
        n = i
        values: dict = {}
        line_review: list[ReviewItem] = []

        # grade: identifiers decide
        grade_id = None
        coating = None
        if lc.grade and quoted(f"{n}.grade", lc.grade_quote) and _loose_in(lc.grade, lc.grade_quote):
            res = kb.resolve(lc.grade)
            coating = res.coating
            if res.grade_id:
                grade_id = res.grade_id
                values["grade_text"] = lc.grade
            else:
                unresolved.append(lc.grade)
                line_review.append(ReviewItem(n, "grade", res.note or f"unknown designation {lc.grade!r}", res.proposals))
        elif "grade" in required:
            line_review.append(ReviewItem(n, "grade", "no grade found in the email"))

        # numbers: read from the quote, not from the model's number
        for fname, qattr in (("thickness_mm", "thickness_quote"), ("width_mm", "width_quote"), ("length_mm", "length_quote"),
                             ("quantity_t", "quantity_quote"), ("max_price_eur_t", "price_quote")):
            q = getattr(lc, qattr)
            claimed = getattr(lc, fname)
            if claimed is None and not q:
                if fname in required:
                    line_review.append(ReviewItem(n, fname, f"{fname.split('_')[0]} not stated"))
                continue
            src = quoted(f"{n}.{fname}", q)
            if not src:
                if fname in required:
                    line_review.append(ReviewItem(n, fname, f"{fname.split('_')[0]}: the quoted text is not in the email"))
                continue
            field_name = "price_eur_t" if fname == "max_price_eur_t" else fname
            r = read_field(field_name, q)
            if r.value is None:
                line_review.append(ReviewItem(n, fname, r.reason))
                continue
            if src in transcribed_sources:
                line_review.append(ReviewItem(n, fname, f"{r.value:g} read from a transcribed image; confirm"))
            values[fname] = r.value

        # coating: from the grade suffix or its own quote
        if lc.coating and quoted(f"{n}.coating", lc.coating_quote):
            m = re.search(r"\b(Z|ZF|ZM|AZ|AS)\s?(\d{2,3})\b", lc.coating_quote, re.I)
            if m:
                coating = f"{m.group(1).upper()}{m.group(2)}"
        if lc.surface and quoted(f"{n}.surface", lc.surface_quote) and _loose_in(lc.surface, lc.surface_quote):
            values["surface"] = lc.surface.upper()

        # form and finish: model reads, code checks the words; finish falls back to the grade family
        form = None
        if lc.form and quoted(f"{n}.form", lc.form_quote) and re.search(_FORM_WORDS[lc.form], lc.form_quote, re.I):
            form = lc.form
        if form is None and "form" in required:
            line_review.append(ReviewItem(n, "form", "coil or sheets not stated"))
        finish = None
        if lc.finish and quoted(f"{n}.finish", lc.finish_quote) and re.search(_FINISH_WORDS[lc.finish], lc.finish_quote, re.I):
            finish = lc.finish
        if grade_id:
            family_finish = _FAMILY_FINISH[kb.get(grade_id).family]
            if finish is None:
                finish = family_finish
            elif finish != family_finish and family_finish in ("coated", "stainless", "cold_rolled"):
                line_review.append(ReviewItem(n, "finish", f"{finish.replace('_', ' ')} does not fit grade {grade_id}"))
        if coating and finish != "coated":
            finish = "coated" if grade_id and _FAMILY_FINISH[kb.get(grade_id).family] == "coated" else finish

        if form in ("sheet", "plate") and values.get("length_mm") is None:
            line_review.append(ReviewItem(n, "length_mm", "sheet length not stated"))

        # sanity: thickness must be smaller than width
        t, w = values.get("thickness_mm"), values.get("width_mm")
        if t is not None and w is not None and t >= w:
            line_review.append(ReviewItem(n, "thickness_mm", f"thickness {t:g} mm is not smaller than width {w:g} mm"))

        alts = []
        for a in lc.alternatives:
            if quoted(f"{n}.alt.{a.kind}", a.quote):
                r = read_field("thickness_mm" if a.kind == "thickness_mm" else "width_mm", a.quote)
                if r.value is not None:
                    alts.append({a.kind: r.value})

        review.extend(line_review)
        if grade_id and form and finish and all(values.get(f) is not None for f in ("thickness_mm", "width_mm", "quantity_t")):
            if t is not None and w is not None and t >= w:
                continue
            lines.append(LineSpec(
                line=n, form=form, finish=finish, grade_id=grade_id, grade_text=values.get("grade_text", ""),
                coating=coating, surface=values.get("surface"), thickness_mm=values["thickness_mm"], width_mm=values["width_mm"],
                length_mm=values.get("length_mm"), quantity_t=values["quantity_t"], max_price_eur_t=values.get("max_price_eur_t"),
                delivery_country=country, delivery_by=by, alternatives=alts))

    if not claims.lines:
        review.append(ReviewItem(0, "lines", "RFQ without a readable product line"))
    status: Literal["ready", "review"] = "review" if review or len(lines) < len(claims.lines) else "ready"
    return Extraction(status, lines, policy, review, evidence, invalid, unresolved, claims.language)


def _loose_in(value: str, quote: str) -> bool:
    norm = lambda s: re.sub(r"[\s\-_.]", "", s).lower()
    return norm(value) in norm(quote)


@dataclass
class ExtractDeps:
    kb: GradeKB


def build_agent(model, instructions: str, retries: int = 2) -> Agent[ExtractDeps, RfqClaims]:
    agent: Agent[ExtractDeps, RfqClaims] = Agent(model, output_type=RfqClaims, instructions=instructions,
                                                 deps_type=ExtractDeps, retries=retries, name="extractor")

    @agent.tool
    def lookup_grade(ctx: RunContext[ExtractDeps], designation: str) -> dict:
        """Check a steel designation against the grade knowledge base."""
        r = ctx.deps.kb.resolve(designation)
        return {"grade_id": r.grade_id, "via": r.via, "proposals": list(r.proposals), "note": r.note}

    return agent


def limits(max_requests: int, max_input_tokens: int) -> UsageLimits:
    return UsageLimits(request_limit=max_requests, input_tokens_limit=max_input_tokens)
