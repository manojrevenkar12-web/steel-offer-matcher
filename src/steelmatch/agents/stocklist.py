"""Supplier stock lists: the model maps columns once per layout, code reads every row.

Suppliers send lists in their own layouts: German or English headers, kg or tonnes, dimensions
in one column or three, prices per tonne or per kilo. The model proposes which column holds
which field. Code checks the proposal against the values (a thickness column must hold
plausible thicknesses, width must exceed thickness, weights must be plausible lot weights) and
reads the rows itself. A mapping that passes is remembered for that layout, so the model is
called once per layout, not once per list. A list that fails is held whole, never half-loaded.
"""

from __future__ import annotations

import hashlib
import re
import statistics
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from ..domain.dates import delivery_by
from ..domain.grades import GradeKB
from ..domain.spec import StockItem
from ..domain.units import dims, readings

# Second choice in the languages supplier lists arrive in (de, en, nl, pl, it, fr, cs).
SECONDARY = r"2\.?\s?wahl|zweite|ii\.?\s|\bii\b|second|2nd|b-ware|2e\s?keus|tweede|ii\s?gatunek|seconda|2e\s?choix|druhá"

FIELDS = ["designation", "dimensions", "thickness", "width", "length", "weight", "price", "form", "coating", "surface",
          "quality", "certificate", "origin", "location", "available", "edge"]


class ColumnMap(BaseModel):
    columns: dict[str, str] = Field(default_factory=dict, description="our field -> header text of the column")
    weight_unit: Literal["t", "kg"] = "t"
    price_unit: Literal["eur_t", "eur_kg"] = "eur_t"
    length_unit: Literal["mm", "m"] = "mm"


@dataclass
class ImportResult:
    status: Literal["loaded", "held"]
    items: list[StockItem]
    mapping: ColumnMap | None
    problems: list[str] = field(default_factory=list)
    skipped_rows: list[tuple[int, str]] = field(default_factory=list)
    used_model: bool = False


def header_row(rows: list[list[str]]) -> int:
    for i, r in enumerate(rows[:10]):
        texts = [c for c in r if c and not re.fullmatch(r"[\d.,\s]+", c)]
        if len(texts) >= 3:
            return i
    return 0


def fingerprint(headers: list[str]) -> str:
    norm = "|".join(re.sub(r"\s+", " ", h.strip().lower()) for h in headers)
    return hashlib.sha256(norm.encode()).hexdigest()[:16]


def build_agent(model, instructions: str) -> Agent[None, ColumnMap]:
    return Agent(model, output_type=ColumnMap, instructions=instructions, retries=1, name="stocklist_mapper")


def mapping_prompt(rows: list[list[str]], h: int) -> str:
    sample = rows[h: h + 6]
    return "Our fields: " + ", ".join(FIELDS) + "\n\nList:\n" + "\n".join(" | ".join(r) for r in sample)


def _col(headers: list[str], name: str | None) -> int | None:
    if not name:
        return None
    norm = name.strip().lower()
    for i, hd in enumerate(headers):
        if hd.strip().lower() == norm:
            return i
    return None


def _num(raw: str, field_name: str, scale: float = 1.0) -> float | None:
    from ..domain.units import plausible

    for v in readings(raw.strip().replace("€", "").replace("EUR", "").strip()):
        v = round(v * scale, 6)
        if plausible(field_name, v):
            return v
    return None


def validate(mapping: ColumnMap, headers: list[str], body: list[list[str]]) -> list[str]:
    """Problems with a proposed mapping, judged on the values. Empty means the mapping holds."""
    problems: list[str] = []
    cols = {f: _col(headers, n) for f, n in mapping.columns.items()}
    for f, n in mapping.columns.items():
        if n and cols[f] is None:
            problems.append(f"column {n!r} for {f} is not in the header")
    if cols.get("designation") is None:
        problems.append("no designation column")
    has_dims = cols.get("dimensions") is not None
    if not has_dims and (cols.get("thickness") is None or cols.get("width") is None):
        problems.append("no thickness and width columns")
    if cols.get("weight") is None or cols.get("price") is None:
        problems.append("no weight or price column")
    if problems:
        return problems
    sample = body[:40]
    t_vals, w_vals, wt_vals, p_vals, l_vals = [], [], [], [], []
    wscale = 0.001 if mapping.weight_unit == "kg" else 1.0
    pscale = 1000.0 if mapping.price_unit == "eur_kg" else 1.0
    lscale = 1000.0 if mapping.length_unit == "m" else 1.0
    for r in sample:
        get = lambda f: r[cols[f]] if cols.get(f) is not None and cols[f] < len(r) else ""
        if has_dims:
            d = dims(get("dimensions")) or {}
            t, w = d.get("thickness_mm"), d.get("width_mm")
        else:
            t, w = _num(get("thickness"), "thickness_mm"), _num(get("width"), "width_mm")
        if t is not None:
            t_vals.append(t)
        if w is not None:
            w_vals.append(w)
        if cols.get("length") is not None and get("length").strip():
            lv = _num(get("length"), "length_mm", lscale)
            if lv is not None:
                l_vals.append(lv)
        wt = _num(get("weight"), "quantity_t", wscale)
        if wt is not None:
            wt_vals.append(wt)
        p = _num(get("price"), "price_eur_t", pscale)
        if p is not None:
            p_vals.append(p)
    n = max(1, len(sample))
    if len(t_vals) < 0.8 * n or len(w_vals) < 0.8 * n:
        problems.append("thickness or width unreadable in most rows")
    if t_vals and w_vals and statistics.median(t_vals) >= statistics.median(w_vals):
        problems.append("thickness values are not smaller than widths: columns swapped?")
    if l_vals and w_vals and statistics.median(l_vals) < statistics.median(w_vals):
        problems.append("lengths are shorter than widths: width and length swapped?")
    if len(wt_vals) < 0.8 * n or (wt_vals and not 0.3 <= statistics.median(wt_vals) <= 40):
        problems.append("lot weights implausible: unit kg or t?")
    if len(p_vals) < 0.8 * n or (p_vals and not 300 <= statistics.median(p_vals) <= 9000):
        problems.append("prices implausible: per tonne or per kilo?")
    return problems


def read_rows(mapping: ColumnMap, headers: list[str], body: list[list[str]], supplier_id: str, supplier_country: str,
              kb: GradeKB, today: date, list_id: str) -> tuple[list[StockItem], list[tuple[int, str]]]:
    from ..agents.extractor import _FAMILY_FINISH

    cols = {f: _col(headers, n) for f, n in mapping.columns.items()}
    wscale = 0.001 if mapping.weight_unit == "kg" else 1.0
    pscale = 1000.0 if mapping.price_unit == "eur_kg" else 1.0
    lscale = 1000.0 if mapping.length_unit == "m" else 1.0
    items: list[StockItem] = []
    skipped: list[tuple[int, str]] = []
    for idx, r in enumerate(body, start=1):
        get = lambda f: (r[cols[f]] if cols.get(f) is not None and cols[f] < len(r) else "").strip()
        designation = get("designation")
        res = kb.resolve(designation)
        form = None
        length = None
        if cols.get("dimensions") is not None:
            d = dims(get("dimensions")) or {}
            t, w = d.get("thickness_mm"), d.get("width_mm")
            length = d.get("length_mm")
            form = d.get("form")
        else:
            t, w = _num(get("thickness"), "thickness_mm"), _num(get("width"), "width_mm")
            if get("length"):
                if get("length").lower() in ("c", "coil", "ring"):
                    form = "coil"
                else:
                    length = _num(get("length"), "length_mm", lscale)
        weight = _num(get("weight"), "quantity_t", wscale)
        price = _num(get("price"), "price_eur_t", pscale)
        if t is None or w is None or weight is None or price is None:
            skipped.append((idx, "unreadable dimension, weight or price"))
            continue
        ftxt = get("form").lower()
        if re.search(r"coil|ring|rolle", ftxt):
            form = "coil"
        elif re.search(r"spaltband|strip|band", ftxt):
            form = "strip"
        elif re.search(r"grobblech|plate", ftxt):
            form = "plate"
        elif re.search(r"blech|tafel|sheet", ftxt):
            form = "sheet"
        if form is None:
            form = "sheet" if length else "coil"
        if res.grade_id:
            finish = _FAMILY_FINISH[kb.get(res.grade_id).family]
        elif res.coating:
            finish = "coated"
        elif re.search(r"1\.4\d{3}|v2a|v4a|aisi", designation, re.I):
            finish = "stainless"
        elif re.search(r"\bDC\d|kaltgewalzt|\bCR\b", designation, re.I):
            finish = "cold_rolled"
        else:
            finish = "hot_rolled"
        coating = res.coating
        cm = re.search(r"\b(Z|ZF|ZM|AZ|AS)\s?(\d{2,3})\b", get("coating"), re.I)
        if cm:
            coating = f"{cm.group(1).upper()}{cm.group(2)}"
        q = get("quality").lower()
        quality = "secondary" if re.search(SECONDARY, q) else "prime"
        cert_m = re.search(r"3\.2|3\.1|2\.2", get("certificate"))
        avail = None
        if get("available"):
            avail, _ = delivery_by(get("available"), today)
        origin = get("origin").upper()[:2] or None
        country = (get("location").upper()[:2] or supplier_country) if cols.get("location") is not None else supplier_country
        items.append(StockItem(
            item_id=f"{list_id}-{idx:04d}", supplier_id=supplier_id, form=form, finish=finish, grade_id=res.grade_id,
            grade_text=designation, coating=coating, surface=(get("surface").upper() or None),
            thickness_mm=t, width_mm=w, length_mm=length if form in ("sheet", "plate") else None, weight_t=weight,
            price_eur_t=price, country=country, origin=origin, available_from=avail,
            certificate=cert_m.group(0) if cert_m else "none", quality=quality,
            edge="mill" if re.search(r"mill|naturkante|nk", get("edge"), re.I) else "trimmed"))
    return items, skipped
