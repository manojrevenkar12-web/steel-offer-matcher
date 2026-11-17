"""Offer texts for the trader to send, written by code from the fit facts.

Every number in an offer text comes from the stock list and the fit rules. The trader edits
the tone, not the facts.
"""

from __future__ import annotations

from ..domain.fit import Fit
from ..domain.spec import LineSpec, StockItem

_T = {
    "de": {"head": "Position {line}: {grade} {dims}, {qty:g} t", "offer": "{n}. {grade} {dims}, {usable:g} t verfügbar, "
           "€{landed:,.0f}/t frei {country}{proc}", "proc": ", inkl. {p}", "upgrade": " (höhere Güte {grade} statt {req})",
           "none": "Für diese Position haben wir aktuell kein passendes Material."},
    "en": {"head": "Line {line}: {grade} {dims}, {qty:g} t", "offer": "{n}. {grade} {dims}, {usable:g} t available, "
           "€{landed:,.0f}/t delivered {country}{proc}", "proc": ", incl. {p}", "upgrade": " (higher grade {grade} instead of {req})",
           "none": "We have no matching material for this line at the moment."},
}
_PROC_DE = {"slitting": "Spalten", "cut to length": "Querteilen", "cross-cutting": "Ablängen"}


def dims(t: float, w: float, l: float | None) -> str:
    return f"{t:g} x {w:g}" + (f" x {l:g}" if l else " x C") + " mm"


def offer_text(spec: LineSpec, offers: list[tuple[Fit, StockItem]], lang: str = "de") -> str:
    t = _T.get(lang, _T["en"])
    lines = [t["head"].format(line=spec.line, grade=spec.grade_id, dims=dims(spec.thickness_mm, spec.width_mm, spec.length_mm),
                              qty=spec.quantity_t)]
    if not offers:
        lines.append(t["none"])
    for n, (f, it) in enumerate(offers, start=1):
        proc = ""
        if f.processing:
            names = [(_PROC_DE.get(p, p) if lang == "de" else p) for p in f.processing]
            proc = t["proc"].format(p=", ".join(names))
        text = t["offer"].format(n=n, grade=it.grade_id or it.grade_text, dims=dims(it.thickness_mm, it.width_mm, it.length_mm),
                                 usable=f.usable_t, landed=f.landed_eur_t, country=spec.delivery_country, proc=proc)
        if f.grade_match == "upgrade":
            text += t["upgrade"].format(grade=it.grade_id, req=spec.grade_id)
        lines.append(text)
    return "\n".join(lines)
