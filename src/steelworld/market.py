"""The simulated steel market: suppliers with stock, buyers with requests, over a working month."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from . import rand
from .catalog import BY_ID, MATERIALS, Material

ROOT = Path(__file__).resolve().parents[2]

COUNTRIES = ["DE", "DE", "DE", "DE", "NL", "BE", "PL", "CZ", "AT", "IT", "FR"]
CITIES = {
    "DE": ["Dortmund", "Duisburg", "Hagen", "Siegen", "Stuttgart", "Nürnberg", "Bielefeld", "Iserlohn", "Gütersloh", "Chemnitz",
           "Mannheim", "Kassel", "Leipzig", "Bremen", "Augsburg"],
    "NL": ["Rotterdam", "Venlo", "Eindhoven"], "BE": ["Antwerpen", "Gent"], "PL": ["Katowice", "Poznań"],
    "CZ": ["Ostrava", "Brno"], "AT": ["Linz", "Graz"], "IT": ["Brescia", "Verona"], "FR": ["Lyon", "Metz"],
}
FIRST = ["Jan", "Petra", "Thomas", "Sabine", "Markus", "Anna", "Lukas", "Claudia", "Stefan", "Julia", "Marco", "Eva", "Tomasz", "Pieter"]
LAST = ["Becker", "Wagner", "Schulz", "Hoffmann", "Kaya", "Nowak", "de Vries", "Bauer", "Klein", "Rossi", "Weber", "Lange"]
CO = ["Metallbau", "Stahlbau", "Blechtechnik", "Maschinenbau", "Anlagenbau", "Fahrzeugtechnik", "Behälterbau", "Lasertechnik"]
SUP = ["Stahlhandel", "Steel Trading", "Metals", "Coil Service", "Blechcenter", "Staal", "Stal", "Acciai"]
HR_WIDTHS = [1000, 1250, 1500, 1520, 1830, 2000, 1190, 1340, 1415]
CR_WIDTHS = [1000, 1250, 1500, 1220, 1340]
LENGTHS = [2000, 2500, 3000, 6000]
THICK = [0.5, 0.6, 0.75, 0.8, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 15.0, 20.0]
MAT_WEIGHTS = {"S235JR": 22, "S355J2": 14, "S235J2": 3, "S275JR": 4, "S355JR": 4, "S355K2": 2, "DD11": 5, "DD13": 3, "DC01": 9,
               "DC04": 4, "DX51D": 10, "DX53D": 3, "S350GD": 3, "1.4301": 6, "1.4404": 2, "1.4016": 2}
LAYOUTS_DEV = ["de_dims_xlsx", "en_csv", "de_split_xlsx", "en_xlsx_m", "nl_csv", "pl_xlsx"]


def settings() -> dict:
    return json.loads((ROOT / "config" / "world.json").read_text())


@dataclass
class Lot:
    lot_id: str
    supplier_id: str
    material: str
    designation: str
    form: str
    thickness_mm: float
    width_mm: float
    length_mm: float | None
    weight_t: float
    price_eur_t: float
    country: str
    origin: str
    available_from: date | None
    cert: str
    quality: str
    edge: str
    coating: str | None
    surface: str | None


@dataclass
class Supplier:
    supplier_id: str
    name: str
    country: str
    layout: str
    lots: list[Lot] = field(default_factory=list)


@dataclass
class Buyer:
    buyer_id: str
    company: str
    contact: str
    city: str
    country: str
    english: bool
    formats: dict[str, float]


@dataclass
class Line:
    material: str
    designation: str
    form: str
    thickness_mm: float
    width_mm: float
    length_mm: float | None
    quantity_t: float
    coating: str | None
    surface: str | None
    alt_thickness_mm: float | None = None
    width_min_mm: float | None = None
    max_price_eur_t: float | None = None
    source_lot: str | None = None


@dataclass
class Policy:
    upgrades_ok: bool = False
    upgrades_ambiguous: bool = False
    secondary_ok: bool = False
    cert: str = "none"
    excluded: tuple[str, ...] = ()
    exclusion_phrase: str = ""


@dataclass
class Rfq:
    message_id: str
    day: int
    minute: int
    buyer: Buyer
    is_rfq: bool
    lines: list[Line]
    policy: Policy
    delivery_by: date | None
    delivery_phrase: str
    fmt: str
    english: bool


@dataclass
class Month:
    seed: int
    today: date
    days: int
    suppliers: list[Supplier]
    buyers: list[Buyer]
    rfqs: list[Rfq]
    heldout: bool
    sold: dict[str, int] = field(default_factory=dict)  # lot_id -> day sold

    def lots(self) -> dict[str, Lot]:
        return {l.lot_id: l for s in self.suppliers for l in s.lots}

    def available(self, day: int) -> list[Lot]:
        return [l for s in self.suppliers for l in s.lots if self.sold.get(l.lot_id, 10**9) > day]


def _material_for(r, thickness: float | None = None) -> Material:
    keys = list(MAT_WEIGHTS)
    while True:
        m = BY_ID[rand.weighted(r, {k: MAT_WEIGHTS[k] for k in keys})]
        if thickness is None or m.thickness_range[0] <= thickness <= m.thickness_range[1]:
            return m


def _designation(r, m: Material, cfg: dict, heldout: bool) -> str:
    style = rand.weighted(r, cfg["designation_variants"])
    if heldout:
        extra = cfg["heldout_only"]["extra_designations"].get(m.id)
        if extra and r.random() < 0.15:
            return r.choice(extra)
    from .catalog import designation

    return designation(m, style, r.choice)


def _lot(seed: int, sup: Supplier, i: int, cfg: dict, heldout: bool, today: date) -> Lot:
    r = rand.of(seed, "lot", sup.supplier_id, i)
    m = _material_for(r)
    lo, hi = m.thickness_range
    t = r.choice([x for x in THICK if lo <= x <= hi])
    form = rand.weighted(r, {"coil": 0.6, "sheet": 0.3, "plate": 0.1 if t >= 5 else 0.0, "strip": 0.06})
    if form == "strip":
        w = float(r.choice([125, 150, 200, 250, 300, 400, 500, 600]))
    else:
        w = float(r.choice(CR_WIDTHS if m.finish in ("cold_rolled", "coated", "stainless") else HR_WIDTHS))
    length = float(r.choice(LENGTHS)) if form in ("sheet", "plate") else None
    weight = round(r.uniform(4, 24) if form in ("coil", "strip") else r.uniform(1.5, 12), 2)
    lr = cfg["lot_rates"]
    quality = "secondary" if r.random() < lr["secondary"] else "prime"
    cert = "3.2" if r.random() < lr["cert_3_2"] else ("3.1" if r.random() < lr["cert_3_1"] else "2.2")
    origin = sup.country
    x = r.random()
    if x < lr["origin_sanctioned"]:
        origin = "RU"
    elif x < lr["origin_sanctioned"] + lr["origin_asian"]:
        origin = r.choice(["CN", "IN", "KR", "VN"])
    price = m.base_price * (1 + max(0.0, (3.0 - t)) * 0.03) * (0.84 if quality == "secondary" else 1.0) * r.uniform(0.92, 1.08)
    if origin in ("CN", "IN", "VN"):
        price *= 0.93
    avail = today + timedelta(days=r.randint(3, 20)) if r.random() < lr["available_later"] else None
    return Lot(
        lot_id=f"{sup.supplier_id}-L{i:03d}", supplier_id=sup.supplier_id, material=m.id,
        designation=_designation(r, m, cfg, heldout), form=form, thickness_mm=t, width_mm=w, length_mm=length,
        weight_t=weight, price_eur_t=round(price, 0), country=sup.country, origin=origin, available_from=avail,
        cert=cert, quality=quality, edge="mill" if r.random() < lr["mill_edge"] and m.finish == "hot_rolled" else "trimmed",
        coating=r.choice(m.coatings) if m.coatings else None, surface=r.choice(m.surfaces) if m.surfaces else None)


def _line_from_lot(r, lot: Lot, cfg: dict, heldout: bool) -> tuple[Line, dict]:
    """A request a lot could serve, sometimes only through slitting, cutting, an upgrade or an alternative."""
    m = BY_ID[lot.material]
    hints: dict = {}
    material = m
    if r.random() < 0.22:
        lower = [x for x in BY_ID.values() if x.id != m.id and x.family == m.family and x.rank < m.rank and not x.replaces]
        if m.replaces:
            lower = [BY_ID[x] for x in m.replaces]
        if lower:
            material = r.choice(lower)
            hints["needs_upgrade"] = True
    form = lot.form
    width = lot.width_mm
    length = lot.length_mm
    if lot.form == "coil" and r.random() < 0.3:
        form = "sheet"
        length = float(r.choice(LENGTHS))
    if lot.form in ("coil", "strip") and r.random() < 0.25 and lot.width_mm >= 500:
        k = r.choice([2, 3, 4])
        width = float(int((lot.width_mm - 10) / k / 5) * 5)
        form = "strip" if width < 650 else form
    thickness = lot.thickness_mm
    alt = None
    if r.random() < cfg["policy_rates"]["alternative_thickness"]:
        others = [x for x in THICK if x != lot.thickness_mm and abs(x - lot.thickness_mm) <= 1.0]
        if others:
            thickness = r.choice(others)
            alt = lot.thickness_mm
    qty = round(max(1.0, lot.weight_t * r.uniform(0.5, 1.05)), 0)
    line = Line(material=material.id, designation=_designation(r, material, cfg, heldout), form=form, thickness_mm=thickness,
                width_mm=width, length_mm=length if form in ("sheet", "plate") else None, quantity_t=qty,
                coating=lot.coating if material.coatings else None, surface=lot.surface if material.surfaces else None,
                alt_thickness_mm=alt, source_lot=lot.lot_id)
    return line, hints


def _random_line(r, cfg: dict, heldout: bool) -> Line:
    m = _material_for(r)
    lo, hi = m.thickness_range
    t = r.choice([x for x in THICK if lo <= x <= hi])
    form = rand.weighted(r, {"coil": 0.5, "sheet": 0.4, "plate": 0.1 if t >= 5 else 0.0})
    w = float(r.choice(CR_WIDTHS if m.finish != "hot_rolled" else HR_WIDTHS[:6]))
    length = float(r.choice(LENGTHS)) if form != "coil" else None
    return Line(material=m.id, designation=_designation(r, m, cfg, heldout), form=form, thickness_mm=t, width_mm=w,
                length_mm=length, quantity_t=float(r.choice([5, 8, 10, 15, 20, 25, 40])),
                coating=r.choice(m.coatings) if m.coatings else None, surface=r.choice(m.surfaces) if m.surfaces else None)


def generate(seed: int, heldout: bool = False) -> Month:
    cfg = settings()
    today = date.fromisoformat(cfg["today"])
    r = rand.of(seed, "month")
    layouts = LAYOUTS_DEV + (cfg["heldout_only"]["extra_layouts"] if heldout else [])
    suppliers = []
    for i in range(cfg["suppliers"]):
        rs = rand.of(seed, "supplier", i)
        country = rs.choice(COUNTRIES)
        layout = layouts[i % len(layouts)] if i < len(layouts) else rs.choice(layouts)
        sup = Supplier(f"S{i:02d}", f"{rs.choice(LAST)} {rs.choice(SUP)}", country, layout)
        n = rs.randint(*cfg["lots_per_supplier"])
        sup.lots = [_lot(seed, sup, j, cfg, heldout, today) for j in range(n)]
        suppliers.append(sup)
    buyers = []
    for i in range(cfg["buyers"]):
        rb = rand.of(seed, "buyer", i)
        country = rb.choice(["DE"] * 6 + ["NL", "AT", "PL", "CZ", "BE", "FR", "IT"])
        english = country not in ("DE", "AT") or rb.random() < cfg["english_share"] * 0.3
        fmts = dict(cfg["formats"])
        fav = rb.choice([k for k in fmts if not k.startswith("_")])
        fmts[fav] *= 3
        buyers.append(Buyer(f"B{i:03d}", f"{rb.choice(LAST)} {rb.choice(CO)} GmbH", f"{rb.choice(FIRST)} {rb.choice(LAST)}",
                            rb.choice(CITIES[country]), country, english, fmts))

    all_lots = [l for s in suppliers for l in s.lots]
    sold: dict[str, int] = {}
    for l in all_lots:
        rl = rand.of(seed, "sold", l.lot_id)
        for d in range(cfg["days"]):
            if rl.random() < cfg["stock_sold_per_day"]:
                sold[l.lot_id] = d
                break

    rfqs: list[Rfq] = []
    pr = cfg["policy_rates"]
    for day in range(cfg["days"]):
        rd = rand.of(seed, "day", day)
        for k in range(rd.randint(*cfg["rfqs_per_day"])):
            rq = rand.of(seed, "rfq", day, k)
            buyer = rq.choice(buyers)
            mid = f"m{seed}-{day:02d}-{k:02d}"
            minute = rq.randint(0, 479)
            fmt = rand.weighted(rq, buyer.formats)
            if rq.random() < cfg["non_rfq_share"]:
                rfqs.append(Rfq(mid, day, minute, buyer, False, [], Policy(), None, "", "prose", buyer.english))
                continue
            n = int(rand.weighted(rq, cfg["lines_per_rfq"]))
            live = [l for l in all_lots if sold.get(l.lot_id, 10**9) > day]
            lines = []
            for _ in range(n):
                if rq.random() < 0.6 and live:
                    line, _hints = _line_from_lot(rq, rq.choice(live), cfg, heldout)
                else:
                    line = _random_line(rq, cfg, heldout)
                lines.append(line)
            pol = Policy()
            if rq.random() < pr["upgrades_ok"]:
                pol.upgrades_ok = True
            elif rq.random() < pr["upgrades_ambiguous"] / (1 - pr["upgrades_ok"]):
                pol.upgrades_ambiguous = True
            pol.secondary_ok = rq.random() < pr["secondary_ok"]
            x = rq.random()
            pol.cert = "3.2" if x < pr["certificate_3_2"] else ("3.1" if x < pr["certificate_3_1"] else "none")
            if rq.random() < pr["excluded_origin"]:
                if rq.random() < 0.5:
                    pol.excluded, pol.exclusion_phrase = ("CN",), "china"
                else:
                    pol.excluded, pol.exclusion_phrase = ("CN", "IN", "KR", "VN"), "asia"
            for line in lines:
                if rq.random() < pr["width_range"] and line.width_mm >= 1000:
                    line.width_min_mm = float(line.width_mm - rq.choice([100, 250]))
                if rq.random() < pr["max_price"]:
                    base = BY_ID[line.material].base_price
                    line.max_price_eur_t = float(round(base * rq.uniform(1.0, 1.18) / 5) * 5)
            by = None
            phrase = "asap"
            if rq.random() < pr["delivery_by"]:
                by = today + timedelta(days=day + rq.randint(7, 30))
                phrase = rq.choice(["week", "date"])
            rfqs.append(Rfq(mid, day, minute, buyer, True, lines, pol, by, phrase, fmt, buyer.english))
    return Month(seed, today, cfg["days"], suppliers, buyers, rfqs, heldout, sold)
