"""What buyers really accept, and what an offer really costs to deliver.

Written for the world, independently of the platform's fit rules. Where the two disagree, the
platform makes real mistakes in the evaluation, as it would in a real market.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

from .catalog import BY_ID, can_replace
from .market import Line, Lot, Policy, Rfq

# Tolerance a buyer really accepts, plus-minus mm by thickness (the world's own table).
_HOT = [(2.0, 0.18), (3.0, 0.2), (5.0, 0.23), (8.0, 0.27), (15.0, 0.36), (25.0, 0.46), (400.0, 0.7)]
_COLD = [(0.4, 0.03), (0.8, 0.05), (1.2, 0.06), (1.6, 0.08), (2.0, 0.1), (3.0, 0.12)]
NEIGHBOURS = {frozenset(p) for p in [("DE", "NL"), ("DE", "BE"), ("DE", "PL"), ("DE", "CZ"), ("DE", "AT"), ("DE", "FR"), ("NL", "BE"),
                                     ("BE", "FR"), ("PL", "CZ"), ("CZ", "AT"), ("AT", "IT"), ("IT", "FR")]}
CERT = {"none": 0, "2.2": 1, "3.1": 2, "3.2": 3}


def band(t: float, finish: str) -> float:
    rows = _COLD if finish in ("cold_rolled", "coated") or (finish == "stainless" and t <= 3) else _HOT
    for mx, b in rows:
        if t <= mx:
            return b
    return rows[-1][1]


def zone(a: str, b: str) -> str:
    if a == b:
        return "same"
    return "neighbour" if frozenset((a, b)) in NEIGHBOURS else "far"


@dataclass(frozen=True)
class TrueFit:
    ok: bool
    landed_eur_t: float
    reason: str


def fit(line: Line, policy: Policy, lot: Lot, buyer_country: str, delivery_by: date | None, today: date, cfg: dict) -> TrueFit:
    tr = cfg["truth"]
    req, off = BY_ID[line.material], BY_ID[lot.material]
    if req.finish != off.finish:
        return TrueFit(False, 0, "finish")
    if lot.material != line.material and not (policy.upgrades_ok and can_replace(line.material, lot.material)):
        return TrueFit(False, 0, "grade")
    if (line.coating or lot.coating) and line.coating != lot.coating:
        if not (line.coating and lot.coating and policy.upgrades_ok and lot.coating[:1] == line.coating[:1]
                and lot.coating.rstrip("0123456789") == line.coating.rstrip("0123456789")
                and int("".join(c for c in lot.coating if c.isdigit())) > int("".join(c for c in line.coating if c.isdigit()))):
            return TrueFit(False, 0, "coating")
    if line.surface and lot.surface and line.surface != lot.surface:
        return TrueFit(False, 0, "surface")
    if line.form in ("coil", "strip") and lot.form in ("sheet", "plate"):
        return TrueFit(False, 0, "form")
    thick_ok = abs(lot.thickness_mm - line.thickness_mm) <= band(line.thickness_mm, req.finish) + 1e-9
    if not thick_ok and line.alt_thickness_mm is not None:
        thick_ok = abs(lot.thickness_mm - line.alt_thickness_mm) <= band(line.alt_thickness_mm, req.finish) + 1e-9
    if not thick_ok:
        return TrueFit(False, 0, "thickness")
    trim = 0.0
    processing = 0.0
    width_need = line.width_mm
    width_min = line.width_min_mm or line.width_mm
    over = 20 if lot.edge == "mill" else 3
    if width_min <= lot.width_mm <= width_need + over:
        pass
    elif lot.width_mm < width_min:
        return TrueFit(False, 0, "width")
    else:
        strips = math.floor((lot.width_mm - tr["edge_trim_mm"]) / width_need)
        if strips < 1:
            return TrueFit(False, 0, "width")
        w_trim = (lot.width_mm - strips * width_need) / lot.width_mm
        if w_trim * 100 > tr["max_trim_pct"]:
            return TrueFit(False, 0, "slitting loss")
        trim = w_trim
        processing += tr["processing_eur_t"]["slitting"]
    if line.form in ("sheet", "plate"):
        if lot.form in ("coil", "strip"):
            processing += tr["processing_eur_t"]["cut_to_length"]
        elif line.length_mm:
            if lot.length_mm is None or lot.length_mm < line.length_mm:
                return TrueFit(False, 0, "length")
            if lot.length_mm > line.length_mm + 10:
                pieces = math.floor(lot.length_mm / line.length_mm)
                l_trim = (lot.length_mm - pieces * line.length_mm) / lot.length_mm
                if l_trim * 100 > tr["max_trim_pct"]:
                    return TrueFit(False, 0, "cutting loss")
                trim = 1 - (1 - trim) * (1 - l_trim)
                processing += tr["processing_eur_t"]["cut_to_length"]
    if lot.quality == "secondary" and not policy.secondary_ok:
        return TrueFit(False, 0, "quality")
    if CERT[lot.cert] < CERT[policy.cert]:
        return TrueFit(False, 0, "certificate")
    if lot.origin in ("RU", "BY") or lot.origin in policy.excluded:
        return TrueFit(False, 0, "origin")
    usable = lot.weight_t * (1 - trim)
    if usable < line.quantity_t * tr["partial_lot_fraction"]:
        return TrueFit(False, 0, "quantity")
    landed = (lot.price_eur_t + processing) / (1 - trim) + tr["freight_eur_t"][zone(lot.country, buyer_country)]
    if line.max_price_eur_t and landed > line.max_price_eur_t * (1 + tr["price_tolerance_pct"] / 100):
        return TrueFit(False, landed, "price")
    if delivery_by:
        days = {"same": 2, "neighbour": 3, "far": 5}[zone(lot.country, buyer_country)] + (1 if processing else 0)
        ready = max(today, lot.available_from or today) + timedelta(days=days)
        if ready > delivery_by + timedelta(days=tr["late_days_accepted"]):
            return TrueFit(False, landed, "late")
    return TrueFit(True, round(landed, 2), "")


def feasible(rfq: Rfq, line: Line, lots: list[Lot], today: date, cfg: dict) -> dict[str, TrueFit]:
    out = {}
    for lot in lots:
        f = fit(line, rfq.policy, lot, rfq.buyer.country, rfq.delivery_by, today, cfg)
        if f.ok:
            out[lot.lot_id] = f
    return out
