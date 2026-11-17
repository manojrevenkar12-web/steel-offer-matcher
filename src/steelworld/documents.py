"""Rendering the world into what a desk receives: stock lists and emails.

While rendering, the world records the exact text it wrote for every value. That record is
what a perfect reader would quote; the simulated models start from it and add their mistakes.
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import rand
from .catalog import BY_ID
from .market import Lot, Month, Rfq, Supplier

# ------------------------------------------------------------------------------------------
# Numbers


def _decimals(x: float) -> int:
    for d in range(0, 4):
        if abs(round(x, d) - x) < 1e-9:
            return d
    return 3


def de(x: float, decimals: int | None = None, thousands: bool = False) -> str:
    if decimals is None:
        decimals = _decimals(x)
    s = f"{x:,.{decimals}f}"
    s = s.replace(",", "_").replace(".", ",").replace("_", "." if thousands else "")
    return s


def en(x: float, decimals: int | None = None) -> str:
    if decimals is None:
        decimals = _decimals(x)
    return f"{x:.{decimals}f}"


# ------------------------------------------------------------------------------------------
# Stock lists

LAYOUTS = {
    "de_dims_xlsx": {"kind": "xlsx", "headers": ["Güte", "Abmessung (mm)", "Gewicht (kg)", "Preis €/t", "Zeugnis", "Wahl", "Herkunft", "Verfügbar ab", "Kante"],
                     "map": {"designation": "Güte", "dimensions": "Abmessung (mm)", "weight": "Gewicht (kg)", "price": "Preis €/t",
                             "certificate": "Zeugnis", "quality": "Wahl", "origin": "Herkunft", "available": "Verfügbar ab", "edge": "Kante"},
                     "weight_unit": "kg", "price_unit": "eur_t", "length_unit": "mm"},
    "en_csv": {"kind": "csv", "headers": ["Grade", "Thickness mm", "Width mm", "Length mm", "Weight t", "Price EUR/t", "Cert", "Quality", "Origin", "Coating"],
               "map": {"designation": "Grade", "thickness": "Thickness mm", "width": "Width mm", "length": "Length mm", "weight": "Weight t",
                       "price": "Price EUR/t", "certificate": "Cert", "quality": "Quality", "origin": "Origin", "coating": "Coating"},
               "weight_unit": "t", "price_unit": "eur_t", "length_unit": "mm"},
    "de_split_xlsx": {"kind": "xlsx", "headers": ["Werkstoff", "Dicke", "Breite", "Länge", "Menge (t)", "€/kg", "Prüfzeugnis", "Qualität", "Oberfläche", "Auflage"],
                      "map": {"designation": "Werkstoff", "thickness": "Dicke", "width": "Breite", "length": "Länge", "weight": "Menge (t)",
                              "price": "€/kg", "certificate": "Prüfzeugnis", "quality": "Qualität", "surface": "Oberfläche", "coating": "Auflage"},
                      "weight_unit": "t", "price_unit": "eur_kg", "length_unit": "mm"},
    "en_xlsx_m": {"kind": "xlsx", "headers": ["Material", "Thk", "Wdth", "Len (m)", "Net kg", "EUR/t", "Certificate", "Choice", "Location", "Form"],
                  "map": {"designation": "Material", "thickness": "Thk", "width": "Wdth", "length": "Len (m)", "weight": "Net kg", "price": "EUR/t",
                          "certificate": "Certificate", "quality": "Choice", "location": "Location", "form": "Form"},
                  "weight_unit": "kg", "price_unit": "eur_t", "length_unit": "m"},
    "nl_csv": {"kind": "csv", "headers": ["Kwaliteit", "Dikte", "Breedte", "Lengte", "Gewicht kg", "Prijs EUR/t", "Certificaat", "Keuze", "Herkomst"],
               "map": {"designation": "Kwaliteit", "thickness": "Dikte", "width": "Breedte", "length": "Lengte", "weight": "Gewicht kg",
                       "price": "Prijs EUR/t", "certificate": "Certificaat", "quality": "Keuze", "origin": "Herkomst"},
               "weight_unit": "kg", "price_unit": "eur_t", "length_unit": "mm"},
    "pl_xlsx": {"kind": "xlsx", "headers": ["Gatunek", "Grubość", "Szerokość", "Długość", "Waga t", "Cena EUR/t", "Atest", "Gatunek handlowy"],
                "map": {"designation": "Gatunek", "thickness": "Grubość", "width": "Szerokość", "length": "Długość", "weight": "Waga t",
                        "price": "Cena EUR/t", "certificate": "Atest", "quality": "Gatunek handlowy"},
                "weight_unit": "t", "price_unit": "eur_t", "length_unit": "mm"},
    "it_xlsx": {"kind": "xlsx", "headers": ["Qualità", "Spessore", "Larghezza", "Lunghezza", "Peso kg", "Prezzo €/t", "Certificato", "Scelta"],
                "map": {"designation": "Qualità", "thickness": "Spessore", "width": "Larghezza", "length": "Lunghezza", "weight": "Peso kg",
                        "price": "Prezzo €/t", "certificate": "Certificato", "quality": "Scelta"},
                "weight_unit": "kg", "price_unit": "eur_t", "length_unit": "mm"},
}


@dataclass
class StockList:
    supplier_id: str
    country: str
    filename: str
    data: bytes
    headers: list[str]
    mapping: dict
    row_lots: list[str]  # data row index (1-based) -> lot id


def _lot_cells(lot: Lot, layout: str) -> dict[str, str]:
    german = layout.startswith("de")
    num = (lambda x, d=None: de(x, d)) if german or layout in ("pl_xlsx", "it_xlsx", "nl_csv") else (lambda x, d=None: en(x, d))
    cells: dict[str, str] = {"designation": lot.designation}
    t = num(lot.thickness_mm, 2 if lot.thickness_mm < 10 else 1)
    w = num(lot.width_mm, 0)
    if lot.coating and layout not in ("en_csv", "de_split_xlsx"):
        cells["designation"] = f"{lot.designation}+{lot.coating}"
    cells["dimensions"] = f"{t} x {w} x " + (num(lot.length_mm, 0) if lot.length_mm else "C")
    cells["thickness"], cells["width"] = t, w
    lo = LAYOUTS[layout]
    if lot.length_mm:
        cells["length"] = num(lot.length_mm / 1000, 1) if lo["length_unit"] == "m" else num(lot.length_mm, 0)
    else:
        cells["length"] = "Coil" if layout != "en_xlsx_m" else ""
    cells["weight"] = num(round(lot.weight_t * 1000), 0) if lo["weight_unit"] == "kg" else num(lot.weight_t, 2)
    cells["price"] = num(lot.price_eur_t / 1000, 3) if lo["price_unit"] == "eur_kg" else num(lot.price_eur_t, 0)
    cells["certificate"] = {"2.2": "2.2", "3.1": "3.1", "3.2": "3.2"}[lot.cert]
    cells["quality"] = {"prime": {"de": "1. Wahl", "en": "prime", "nl": "1e keus", "pl": "I gatunek", "it": "prima"},
                        "secondary": {"de": "2. Wahl", "en": "secondary", "nl": "2e keus", "pl": "II gatunek", "it": "seconda"}}[lot.quality][layout[:2]]
    cells["origin"] = lot.origin
    cells["location"] = lot.country
    cells["available"] = lot.available_from.strftime("%d.%m.%Y") if lot.available_from else "sofort"
    cells["edge"] = "Naturkante" if lot.edge == "mill" else "besäumt"
    cells["coating"] = lot.coating or ""
    cells["surface"] = lot.surface or ""
    cells["form"] = {"coil": "Coil", "strip": "Strip", "sheet": "Sheet", "plate": "Plate"}[lot.form]
    return cells


def stock_list(sup: Supplier, lots: list[Lot], version: int) -> StockList:
    lo = LAYOUTS[sup.layout]
    inv = {v: k for k, v in lo["map"].items()}
    rows = [lo["headers"]]
    row_lots = []
    for lot in lots:
        cells = _lot_cells(lot, sup.layout)
        rows.append([cells.get(inv[h], "") for h in lo["headers"]])
        row_lots.append(lot.lot_id)
    mapping = {"columns": dict(lo["map"]), "weight_unit": lo["weight_unit"], "price_unit": lo["price_unit"], "length_unit": lo["length_unit"]}
    name = f"{sup.supplier_id}_stock_v{version}"
    if lo["kind"] == "csv":
        buf = io.StringIO()
        csv.writer(buf, delimiter=";").writerows(rows)
        return StockList(sup.supplier_id, sup.country, name + ".csv", buf.getvalue().encode(), lo["headers"], mapping, row_lots)
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Lager"
    ws.append([f"Lagerliste {sup.name}"])
    for r in rows:
        ws.append(r)
    out = io.BytesIO()
    wb.save(out)
    return StockList(sup.supplier_id, sup.country, name + ".xlsx", out.getvalue(), lo["headers"], mapping, row_lots)


# ------------------------------------------------------------------------------------------
# RFQ emails

FORM_WORDS = {
    "de": {"coil": "Coils", "sheet": "Bleche", "plate": "Grobbleche", "strip": "Spaltband"},
    "en": {"coil": "coils", "sheet": "sheets", "plate": "plates", "strip": "slit strip"},
}
FINISH_WORDS = {
    "de": {"hot_rolled": "warmgewalzt", "cold_rolled": "kaltgewalzt", "coated": "feuerverzinkt", "stainless": "Edelstahl"},
    "en": {"hot_rolled": "hot rolled", "cold_rolled": "cold rolled", "coated": "hot-dip galvanised", "stainless": "stainless"},
}


@dataclass
class RfqDoc:
    message: dict
    claims: dict  # what a perfect reader returns, with exact quotes
    images: dict[str, str] = field(default_factory=dict)  # image sha256 -> exact text


def _dims(line, lang: str, r) -> str:
    num = de if lang == "de" else en
    t = num(line.thickness_mm)
    if lang == "de" and r.random() < 0.3 and float(line.thickness_mm).is_integer():
        t = de(line.thickness_mm, 1)
    w = de(line.width_mm, 0, thousands=r.random() < 0.35) if lang == "de" else en(line.width_mm, 0)
    tail = num(line.length_mm, 0) if line.length_mm else ("C" if r.random() < 0.6 else ("Coil" if lang == "de" else "coil"))
    return f"{t} x {w} x {tail}" + (" mm" if r.random() < 0.4 else "")


def _qty(q: float, lang: str, r) -> str:
    if lang == "de":
        return r.choice([f"{de(q)} t", f"{de(q)} to", f"ca. {de(q)} to", f"{de(q * 1000, 0, thousands=True)} kg"])
    return r.choice([f"{en(q)} t", f"{en(q)} mt", f"approx. {en(q)} tons"])


def _delivery(rfq: Rfq, lang: str, today: date, r) -> str:
    if rfq.delivery_by is None:
        return "asap" if lang == "en" else r.choice(["sofort", "asap"])
    if rfq.delivery_phrase == "week":
        wk = rfq.delivery_by.isocalendar().week
        return f"KW {wk}" if lang == "de" else f"week {wk}"
    return rfq.delivery_by.strftime("%d.%m.") if lang == "de" else rfq.delivery_by.isoformat()


def render_rfq(month: Month, rfq: Rfq) -> RfqDoc:
    r = rand.of(month.seed, "render", rfq.message_id)
    lang = "en" if rfq.english else "de"
    b = rfq.buyer
    received = (month.today + timedelta(days=rfq.day)).isoformat()
    if not rfq.is_rfq:
        body = r.choice([
            "Sehr geehrte Damen und Herren,\n\nanbei die Auftragsbestätigung zu Ihrer Bestellung 4711-23. Die Ware geht KW 49 raus.\n\nGruß",
            "Hello,\n\nplease find attached our invoice for the last delivery of 22 t S235JR coils. Payment within 30 days.\n\nBest regards",
            "Newsletter: Stahlpreise im November — Warmband stabil, Edelstahl-Legierungszuschlag steigt.",
            "Hallo,\n\ndie Lieferung von gestern hatte Kantenrisse bei 2 Coils S355J2 3,0 x 1500. Bitte um Rückruf.\n\nGruß",
        ])
        return RfqDoc({"message_id": rfq.message_id, "sender": f"{b.contact} <info@{b.buyer_id.lower()}.example>",
                       "subject": "AW: Bestellung" if "Auftrag" in body else "Info", "body": body, "received": received,
                       "attachments": []}, {"is_rfq": False, "language": lang})

    lines_claims = []
    rendered_lines = []
    for i, line in enumerate(rfq.lines, start=1):
        m = BY_ID[line.material]
        dims = _dims(line, lang, r)
        qty = _qty(line.quantity_t, lang, r)
        form_word = FORM_WORDS[lang][line.form]
        finish_word = FINISH_WORDS[lang][m.finish]
        grade = line.designation
        coat = f" {line.coating}" if line.coating else ""
        surf = f" {line.surface}" if line.surface else ""
        claim = {"line": i, "form": line.form, "form_quote": form_word, "finish": m.finish, "finish_quote": finish_word,
                 "grade": grade, "grade_quote": grade, "thickness_mm": line.thickness_mm, "thickness_quote": dims,
                 "width_mm": line.width_mm, "width_quote": dims, "quantity_t": line.quantity_t, "quantity_quote": qty,
                 "alternatives": []}
        if line.length_mm:
            claim.update({"length_mm": line.length_mm, "length_quote": dims})
        if line.coating:
            claim.update({"coating": line.coating, "coating_quote": line.coating})
        if line.surface:
            claim.update({"surface": line.surface, "surface_quote": f"Oberfläche {line.surface}" if lang == "de" else f"finish {line.surface}"})
        extras = []
        if line.alt_thickness_mm:
            a = f"alternativ auch {de(line.alt_thickness_mm)} mm" if lang == "de" else f"{en(line.alt_thickness_mm)} mm would also work"
            extras.append(a)
            claim["alternatives"].append({"kind": "thickness_mm", "value": line.alt_thickness_mm, "quote": a})
        if line.width_min_mm:
            a = f"Breite ab {de(line.width_min_mm, 0)} mm auch möglich" if lang == "de" else f"widths from {en(line.width_min_mm, 0)} mm also work"
            extras.append(a)
            claim["alternatives"].append({"kind": "width_min_mm", "value": line.width_min_mm, "quote": a})
        if line.max_price_eur_t:
            p = f"Zielpreis {de(line.max_price_eur_t, 0)} €/t" if lang == "de" else f"target price EUR {en(line.max_price_eur_t, 0)}/t"
            extras.append(p)
            claim.update({"max_price_eur_t": line.max_price_eur_t, "price_quote": p})
        lines_claims.append(claim)
        rendered_lines.append({"i": i, "grade": grade + coat, "dims": dims, "qty": qty, "form": form_word, "finish": finish_word,
                               "surface": claim.get("surface_quote", ""), "extras": extras})

    pol = rfq.policy
    pol_sent: list[str] = []
    policy_claim = {"upgrades_ok": False, "secondary_ok": False, "certificate": "none", "excluded_origins": []}
    if pol.upgrades_ok:
        s = r.choice(["Höhere Güte ist auch möglich.", "Gerne auch in höherer Güte."]) if lang == "de" else "A higher grade is fine as well."
        pol_sent.append(s)
        policy_claim.update({"upgrades_ok": True, "upgrades_quote": s})
    elif pol.upgrades_ambiguous:
        s = "Gleichwertige Güte möglich." if lang == "de" else "Equivalent grade acceptable."
        pol_sent.append(s)  # not an upgrade: equivalent means the same strength, not a higher one
    if pol.secondary_ok:
        s = "2. Wahl ist für uns auch interessant." if lang == "de" else "Second choice is acceptable."
        pol_sent.append(s)
        policy_claim.update({"secondary_ok": True, "secondary_quote": s})
    if pol.cert != "none":
        s = f"Mit APZ {pol.cert} nach EN 10204." if lang == "de" else f"With {pol.cert} certificate to EN 10204."
        pol_sent.append(s)
        policy_claim.update({"certificate": pol.cert, "certificate_quote": s})
    if pol.excluded:
        if pol.exclusion_phrase == "china":
            s = "Bitte keine Ware aus China." if lang == "de" else "No material of Chinese origin, please."
        else:
            s = "Bitte keine Asienware." if lang == "de" else "No Asian material, please."
        pol_sent.append(s)
        policy_claim.update({"excluded_origins": list(pol.excluded), "origins_quote": s})

    delivery = _delivery(rfq, lang, month.today + timedelta(days=rfq.day), r)
    place = f"frei Werk {b.city}" if lang == "de" else f"delivered {b.city}"
    claims = {"is_rfq": True, "language": lang, "lines": lines_claims, "policy": policy_claim,
              "delivery_country": b.country, "delivery_place_quote": place,
              "delivery_quote": f"Lieferung {delivery}" if lang == "de" else f"delivery {delivery}", "questions": []}
    if rfq.delivery_by is None:
        claims["delivery_quote"] = claims["delivery_quote"]

    fmt = rfq.fmt if rfq.fmt != "image" or len(rfq.lines) <= 3 else "inline_table"
    hello = "Sehr geehrte Damen und Herren," if lang == "de" else "Dear Sir or Madam,"
    bye = f"Mit freundlichen Grüßen\n{b.contact}\n{b.company}\n{b.city}" if lang == "de" else f"Best regards\n{b.contact}\n{b.company}\n{b.city}"
    ask = "bitte bieten Sie an:" if lang == "de" else "please quote:"
    ref = f"{'Anfrage-Nr.' if lang == 'de' else 'Enquiry no.'} {rfq.message_id.split('-', 1)[1].replace('-', '')}{b.buyer_id[1:]}"
    tail = "\n".join(pol_sent + [f"{claims['delivery_quote']} {place}.", ref])
    attachments = []
    images: dict[str, str] = {}
    table_lines = []
    for rl in rendered_lines:
        extra = ("; " + "; ".join(rl["extras"])) if rl["extras"] else ""
        surf = f", {rl['surface']}" if rl["surface"] else ""
        table_lines.append(f"{rl['i']}. {rl['qty']} {rl['form']} {rl['finish']} {rl['grade']} {rl['dims']}{surf}{extra}")
    table_text = "\n".join(table_lines)
    if fmt == "prose":
        if len(rendered_lines) == 1:
            rl = rendered_lines[0]
            extra = (" " + ", ".join(rl["extras"]) + ".") if rl["extras"] else ""
            surf = f", {rl['surface']}" if rl["surface"] else ""
            sent = (f"wir benötigen {rl['qty']} {rl['form']} {rl['finish']} in {rl['grade']}, {rl['dims']}{surf}.{extra}" if lang == "de"
                    else f"we need {rl['qty']} of {rl['finish']} {rl['form']} in {rl['grade']}, {rl['dims']}{surf}.{extra}")
            body = f"{hello}\n\n{sent}\n{tail}\n\n{bye}"
        else:
            body = f"{hello}\n\n{ask}\n{table_text}\n\n{tail}\n\n{bye}"
    elif fmt == "inline_table":
        body = f"{hello}\n\n{ask}\n\n{table_text}\n\n{tail}\n\n{bye}"
    elif fmt == "xlsx":
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.append(["Pos", "Menge" if lang == "de" else "Qty", "Ausführung" if lang == "de" else "Product", "Güte" if lang == "de" else "Grade",
                   "Abmessung" if lang == "de" else "Size", "Bemerkung" if lang == "de" else "Notes"])
        for rl in rendered_lines:
            ws.append([rl["i"], rl["qty"], f"{rl['form']} {rl['finish']}", rl["grade"], rl["dims"] + (f", {rl['surface']}" if rl["surface"] else ""),
                       "; ".join(rl["extras"])])
        buf = io.BytesIO()
        wb.save(buf)
        attachments.append({"filename": "Anfrage.xlsx" if lang == "de" else "enquiry.xlsx",
                            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "data": buf.getvalue()})
        body = f"{hello}\n\n{'anbei unsere Anfrage.' if lang == 'de' else 'please find our enquiry attached.'}\n{tail}\n\n{bye}"
    elif fmt == "pdf":
        attachments.append({"filename": "Anfrage.pdf" if lang == "de" else "RFQ.pdf", "content_type": "application/pdf",
                            "data": _pdf(table_lines)})
        body = f"{hello}\n\n{'Anfrage siehe PDF.' if lang == 'de' else 'RFQ in the attached PDF.'}\n{tail}\n\n{bye}"
    else:  # image of a printed enquiry
        png = _png(table_lines)
        images[hashlib.sha256(png).hexdigest()] = table_text
        attachments.append({"filename": "scan.png", "content_type": "image/png", "data": png})
        body = f"{hello}\n\n{'Anfrage als Foto anbei.' if lang == 'de' else 'Photo of our enquiry attached.'}\n{tail}\n\n{bye}"
    subject = ("Anfrage" if lang == "de" else "RFQ") + f" {rendered_lines[0]['grade']}"
    msg = {"message_id": rfq.message_id, "sender": f"{b.contact} <einkauf@{b.buyer_id.lower()}.example>", "subject": subject,
           "body": body, "received": received, "attachments": attachments}
    return RfqDoc(msg, claims, images)


def _pdf(lines: list[str]) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 10)
    y = 800
    c.drawString(50, y, "Anfrage / Enquiry")
    for ln in lines:
        y -= 18
        c.drawString(50, y, ln)
    c.save()
    return buf.getvalue()


def _png(lines: list[str]) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("L", (900, 40 + 22 * len(lines)), 255)
    d = ImageDraw.Draw(img)
    for i, ln in enumerate(lines):
        d.text((20, 20 + 22 * i), ln, fill=0)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
