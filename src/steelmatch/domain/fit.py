"""Does a stock lot satisfy a requested line? Code decides, check by check.

Every check returns pass, review or fail with a reason a trader can read. A lot is offered only
when no check fails; it goes to a person when any check says review. Processing (slitting a
wider coil, cutting a coil to sheets) is costed here, so the ranking compares landed cost per
usable tonne rather than list price.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Literal

from .grades import GradeKB
from .spec import CERT_RANK, LineSpec, Policy, StockItem

ROOT = Path(__file__).resolve().parents[3]
Status = Literal["pass", "review", "fail"]


@dataclass(frozen=True)
class FitSettings:
    max_trim_pct: float = 6.0
    edge_trim_mill_mm: float = 10.0
    edge_trim_trimmed_mm: float = 4.0
    full_lot_fraction: float = 0.9
    partial_lot_fraction: float = 0.3
    slitting_eur_t: float = 18.0
    cut_to_length_eur_t: float = 22.0
    price_review_pct: float = 3.0
    late_review_days: int = 7
    sanctioned_origins: tuple[str, ...] = ("RU", "BY")

    @classmethod
    def from_dict(cls, d: dict) -> "FitSettings":
        d = dict(d)
        if "sanctioned_origins" in d:
            d["sanctioned_origins"] = tuple(d["sanctioned_origins"])
        return cls(**d)


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str


@dataclass
class Fit:
    item_id: str
    status: Status
    checks: list[Check]
    usable_t: float
    landed_eur_t: float
    processing: list[str] = field(default_factory=list)
    grade_match: Literal["same", "upgrade", "unknown", "no"] = "no"
    partial: bool = False
    lead_days: int = 0

    def explain(self) -> list[str]:
        return [f"{c.name}: {c.detail}" for c in self.checks if c.status != "pass"] or ["meets every requirement"]


@lru_cache(maxsize=1)
def _tolerances() -> dict:
    return json.loads((ROOT / "knowledge" / "tolerances.json").read_text())


@lru_cache(maxsize=1)
def _freight() -> dict:
    return json.loads((ROOT / "knowledge" / "freight.json").read_text())


def thickness_band(thickness_mm: float, finish: str) -> float:
    table = _tolerances()["thickness"]
    rows = table["cold_rolled"] if finish in ("cold_rolled", "coated") or (finish == "stainless" and thickness_mm <= 3) else table["hot_rolled"]
    for row in rows:
        if thickness_mm <= row["max_mm"]:
            return row["band_mm"]
    return rows[-1]["band_mm"]


def freight_eur_t(src: str, dst: str) -> float:
    table = _freight()["eur_per_t"]
    return float(table.get(src, {}).get(dst) or table.get(dst, {}).get(src) or 80.0)


def transit_days(src: str, dst: str) -> int:
    f = _freight()
    if src == dst:
        return f["transit_days"]["same"]
    if [src, dst] in f["neighbours"] or [dst, src] in f["neighbours"]:
        return f["transit_days"]["neighbour"]
    return f["transit_days"]["far"]


def _worst(checks: list[Check]) -> Status:
    if any(c.status == "fail" for c in checks):
        return "fail"
    if any(c.status == "review" for c in checks):
        return "review"
    return "pass"


def check(spec: LineSpec, policy: Policy, item: StockItem, kb: GradeKB, s: FitSettings, today: date) -> Fit:
    checks: list[Check] = []
    processing: list[str] = []
    trim = 0.0

    # Finish and form
    if item.finish != spec.finish:
        checks.append(Check("finish", "fail", f"{item.finish.replace('_', ' ')} offered, {spec.finish.replace('_', ' ')} requested"))
    cut_forms = ("sheet", "plate")
    if spec.form in cut_forms and item.form in ("coil", "strip"):
        if policy.coil_for_sheet_ok:
            processing.append("cut to length")
            checks.append(Check("form", "pass", f"{item.form} cut to {spec.form}s"))
        else:
            checks.append(Check("form", "fail", f"{item.form} offered, buyer wants {spec.form}s only"))
    elif spec.form in ("coil", "strip") and item.form in cut_forms:
        checks.append(Check("form", "fail", f"{item.form}s cannot become a {spec.form}"))
    else:
        checks.append(Check("form", "pass", item.form))

    # Grade: identifiers decide
    grade_match: Literal["same", "upgrade", "unknown", "no"]
    if item.grade_id is None:
        grade_match = "unknown"
        checks.append(Check("grade", "review", f"supplier designation {item.grade_text!r} is not a known grade"))
    else:
        kind = kb.upgrade_kind(spec.grade_id, item.grade_id)
        if kind == "same":
            grade_match = "same"
            checks.append(Check("grade", "pass", item.grade_id))
        elif kind == "upgrade":
            grade_match = "upgrade"
            req, off = kb.get(spec.grade_id), kb.get(item.grade_id)
            note = f"{item.grade_id} instead of {spec.grade_id}"
            if req.cev_max is not None and off.cev_max is not None and off.cev_max > req.cev_max:
                note += f"; carbon equivalent up to {off.cev_max:.2f} instead of {req.cev_max:.2f}, check welding"
            checks.append(Check("grade", "pass" if policy.upgrades_ok else "review",
                                note + ("" if policy.upgrades_ok else "; buyer has not said a higher grade is fine")))
        else:
            grade_match = "no"
            checks.append(Check("grade", "fail", f"{item.grade_id} cannot replace {spec.grade_id}"))

    # Coating
    if spec.coating or item.coating:
        if not item.coating:
            checks.append(Check("coating", "fail", f"uncoated, {spec.coating} requested"))
        elif not spec.coating:
            checks.append(Check("coating", "fail", f"{item.coating} coated, uncoated requested"))
        else:
            rt, rm = _split_coating(spec.coating)
            ot, om = _split_coating(item.coating)
            if rt != ot:
                checks.append(Check("coating", "fail", f"{item.coating} is a different coating system from {spec.coating}"))
            elif om < rm:
                checks.append(Check("coating", "fail", f"{item.coating} is lighter than {spec.coating}"))
            elif om > rm:
                checks.append(Check("coating", "pass" if policy.upgrades_ok else "review", f"heavier {item.coating} instead of {spec.coating}"))
            else:
                checks.append(Check("coating", "pass", item.coating))

    # Surface finish (stainless and coated): unknown is not a pass
    if spec.surface:
        if not item.surface:
            checks.append(Check("surface", "review", f"list does not state the surface, {spec.surface} requested"))
        elif spec.surface.upper() != item.surface.upper():
            checks.append(Check("surface", "fail", f"surface {item.surface}, {spec.surface} requested"))

    # Thickness, with alternatives the buyer named
    band = thickness_band(spec.thickness_mm, spec.finish)
    ok_t = abs(item.thickness_mm - spec.thickness_mm) <= band + 1e-9
    alt_t = [a["thickness_mm"] for a in spec.alternatives if "thickness_mm" in a]
    if ok_t:
        checks.append(Check("thickness", "pass", f"{item.thickness_mm:g} mm within ±{band:g} mm of {spec.thickness_mm:g}"))
    elif any(abs(item.thickness_mm - t) <= thickness_band(t, spec.finish) + 1e-9 for t in alt_t):
        checks.append(Check("thickness", "pass", f"{item.thickness_mm:g} mm, an alternative the buyer named"))
    else:
        checks.append(Check("thickness", "fail", f"{item.thickness_mm:g} mm, {spec.thickness_mm:g} ±{band:g} mm requested"))

    # Width: equal, or wider and slit
    over = _tolerances()["width"]["mill_edge_over_mm" if item.edge == "mill" else "trimmed_over_mm"]
    width_min = min([spec.width_mm] + [a["width_min_mm"] for a in spec.alternatives if "width_min_mm" in a])
    if width_min <= item.width_mm <= spec.width_mm + over:
        checks.append(Check("width", "pass", f"{item.width_mm:g} mm"))
    elif item.width_mm < width_min:
        checks.append(Check("width", "fail", f"{item.width_mm:g} mm is narrower than {width_min:g} mm"))
    elif not policy.slitting_ok:
        checks.append(Check("width", "fail", f"{item.width_mm:g} mm, buyer does not accept slit material"))
    else:
        edge = s.edge_trim_mill_mm if item.edge == "mill" else s.edge_trim_trimmed_mm
        strips = math.floor((item.width_mm - edge) / spec.width_mm)
        if strips < 1:
            checks.append(Check("width", "fail", f"{item.width_mm:g} mm cannot be slit to {spec.width_mm:g} mm"))
        else:
            w_trim = (item.width_mm - strips * spec.width_mm) / item.width_mm
            if w_trim * 100 > s.max_trim_pct:
                checks.append(Check("width", "fail", f"slitting {item.width_mm:g} mm to {strips}×{spec.width_mm:g} mm loses {w_trim:.1%}"))
            else:
                trim = 1 - (1 - trim) * (1 - w_trim)
                processing.append("slitting")
                checks.append(Check("width", "pass", f"{item.width_mm:g} mm slit to {strips}×{spec.width_mm:g} mm, {w_trim:.1%} trim"))

    # Length for cut products
    if spec.form in cut_forms and spec.length_mm and item.form in cut_forms:
        l_over = _tolerances()["length"]["over_mm"]
        if item.length_mm is None or item.length_mm < spec.length_mm:
            checks.append(Check("length", "fail", f"sheets of {item.length_mm or 0:g} mm are shorter than {spec.length_mm:g} mm"))
        elif item.length_mm <= spec.length_mm + l_over:
            checks.append(Check("length", "pass", f"{item.length_mm:g} mm"))
        else:
            pieces = math.floor(item.length_mm / spec.length_mm)
            l_trim = (item.length_mm - pieces * spec.length_mm) / item.length_mm
            if l_trim * 100 > s.max_trim_pct:
                checks.append(Check("length", "fail", f"cutting {item.length_mm:g} mm sheets to {spec.length_mm:g} mm loses {l_trim:.1%}"))
            else:
                trim = 1 - (1 - trim) * (1 - l_trim)
                processing.append("cross-cutting")
                checks.append(Check("length", "pass", f"{item.length_mm:g} mm cut to {pieces}×{spec.length_mm:g} mm"))

    # Quality, certificate, origin
    if item.quality == "secondary" and not policy.secondary_ok:
        checks.append(Check("quality", "fail", "second-choice material, buyer wants prime"))
    if CERT_RANK[item.certificate] < CERT_RANK[policy.certificate]:
        checks.append(Check("certificate", "fail", f"certificate {item.certificate}, {policy.certificate} required"))
    if item.origin and item.origin in s.sanctioned_origins:
        checks.append(Check("origin", "fail", f"origin {item.origin} is under EU import sanctions for steel"))
    elif item.origin and item.origin in policy.excluded_origins:
        checks.append(Check("origin", "fail", f"buyer excludes origin {item.origin}"))
    elif not item.origin and policy.excluded_origins:
        checks.append(Check("origin", "review", "list does not state the origin and the buyer excludes " + ", ".join(policy.excluded_origins)))

    # Quantity, on usable tonnes after trim
    usable = round(item.weight_t * (1 - trim), 3)
    partial = False
    if usable >= spec.quantity_t * s.full_lot_fraction:
        checks.append(Check("quantity", "pass", f"{usable:g} t usable for {spec.quantity_t:g} t"))
    elif usable >= spec.quantity_t * s.partial_lot_fraction:
        partial = True
        checks.append(Check("quantity", "pass", f"{usable:g} t covers {usable / spec.quantity_t:.0%} of {spec.quantity_t:g} t"))
    else:
        checks.append(Check("quantity", "fail", f"{usable:g} t usable, {spec.quantity_t:g} t requested"))

    # Delivery
    lead = transit_days(item.country, spec.delivery_country) + (1 if processing else 0)
    ready = max(today, item.available_from or today) + timedelta(days=lead)
    if spec.delivery_by and ready > spec.delivery_by:
        late = (ready - spec.delivery_by).days
        status: Status = "review" if late <= s.late_review_days else "fail"
        checks.append(Check("delivery", status, f"arrives {ready.isoformat()}, {late} days after {spec.delivery_by.isoformat()}"))

    # Landed cost per usable tonne
    proc = (s.slitting_eur_t if "slitting" in processing else 0) + (s.cut_to_length_eur_t if "cut to length" in processing else 0) \
        + (s.cut_to_length_eur_t if "cross-cutting" in processing else 0)
    landed = (item.price_eur_t + proc) / max(1e-9, 1 - trim) + freight_eur_t(item.country, spec.delivery_country)
    landed = round(landed, 2)
    if spec.max_price_eur_t is not None and landed > spec.max_price_eur_t:
        over_pct = (landed / spec.max_price_eur_t - 1) * 100
        status = "review" if over_pct <= s.price_review_pct else "fail"
        checks.append(Check("price", status, f"€{landed:,.0f}/t landed, target €{spec.max_price_eur_t:,.0f}/t ({over_pct:+.1f}%)"))
    else:
        checks.append(Check("price", "pass", f"€{landed:,.0f}/t landed"))

    return Fit(item_id=item.item_id, status=_worst(checks), checks=checks, usable_t=usable, landed_eur_t=landed,
               processing=processing, grade_match=grade_match, partial=partial, lead_days=(ready - today).days)


def _split_coating(c: str) -> tuple[str, int]:
    letters = "".join(ch for ch in c if ch.isalpha()).upper()
    digits = "".join(ch for ch in c if ch.isdigit())
    return letters, int(digits or 0)


def rank(fits: list[Fit]) -> list[Fit]:
    """Offers first, then review cases; cheapest landed cost per usable tonne, exact grade, no processing, fastest."""
    order = {"pass": 0, "review": 1, "fail": 2}
    return sorted(fits, key=lambda f: (order[f.status], f.partial, round(f.landed_eur_t, 0),
                                       f.grade_match != "same", len(f.processing), f.lead_days, f.item_id))
