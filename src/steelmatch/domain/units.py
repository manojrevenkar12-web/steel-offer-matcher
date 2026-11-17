"""Reading numbers the way steel traders write them.

Trader text mixes German and English conventions: "1,5" is one and a half, "1.500" is
usually fifteen hundred, "20 to" is twenty tonnes, "2,0 x 1250 x C" is a coil 2 mm thick and
1250 mm wide. The model points at the text that holds a value; this module reads the value
from that text. When a reading is ambiguous the caller gets every plausible reading and
decides, usually by sending the field to review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Plausible ranges per field, used to choose between "1.500" = 1.5 and "1.500" = 1500.
PLAUSIBLE = {
    "thickness_mm": (0.15, 250.0),
    "width_mm": (10.0, 3500.0),
    "length_mm": (100.0, 16000.0),
    "quantity_t": (0.05, 5000.0),
    "price_eur_t": (100.0, 12000.0),
}

_NUM = r"\d{1,3}(?:[.\s]\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?"
_NUM_RE = re.compile(_NUM)


def readings(token: str) -> list[float]:
    """Every value a written number can mean, most likely first.

    "1,5" -> [1.5]; "1.500" -> [1500.0, 1.5]; "1.250,5" -> [1250.5]; "12" -> [12.0];
    "2.5" -> [2.5]; "20.000" -> [20000.0, 20.0].
    """
    t = token.strip().replace(" ", " ")
    if re.fullmatch(r"\d{1,3}(?:[.\s]\d{3})+,\d+", t):  # 1.250,5 German thousands + decimal
        return [float(t.replace(".", "").replace(" ", "").replace(",", "."))]
    if re.fullmatch(r"\d{1,3}(?:\s\d{3})+", t):  # 20 000
        return [float(t.replace(" ", ""))]
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", t):  # 1.500 thousands or decimal
        out = [float(t.replace(".", ""))]
        if t.count(".") == 1:
            out.append(float(t))
        return out
    if re.fullmatch(r"\d+,\d+", t):
        return [float(t.replace(",", "."))]
    if re.fullmatch(r"\d+(?:\.\d+)?", t):
        return [float(t)]
    return []


def plausible(field: str, value: float) -> bool:
    lo, hi = PLAUSIBLE.get(field, (float("-inf"), float("inf")))
    return lo <= value <= hi


@dataclass(frozen=True)
class Reading:
    value: float | None
    ambiguous: bool
    reason: str = ""


_DIMS_RE = re.compile(
    rf"(?P<t>{_NUM})\s*(?:mm)?\s*[x×*]\s*(?P<w>{_NUM})\s*(?:mm)?(?:\s*[x×*]\s*(?P<l>{_NUM}|C|coil|Coil|COIL|Ring))?",
)


def dims(text: str) -> dict[str, float | str] | None:
    """Read a 't x w x l' block. Industry order is thickness, width, length; 'C' means coil."""
    m = _DIMS_RE.search(text)
    if not m:
        return None
    out: dict[str, float | str] = {}
    for key, field in (("t", "thickness_mm"), ("w", "width_mm"), ("l", "length_mm")):
        raw = m.group(key)
        if raw is None:
            continue
        if raw.lower() in ("c", "coil", "ring"):
            out["form"] = "coil"
            continue
        vals = [v for v in readings(raw) if plausible(field, v)]
        if len(vals) != 1:
            return None
        out[field] = vals[0]
    return out


_UNIT_HINTS = {
    "thickness_mm": [(r"\bcm\b", 10.0), (r"\bmm\b", 1.0)],
    "width_mm": [(r"\bcm\b", 10.0), (r"\bm\b", 1000.0), (r"\bmm\b", 1.0)],
    "length_mm": [(r"\bcm\b", 10.0), (r"\bm\b", 1000.0), (r"\bmm\b", 1.0)],
    "quantity_t": [(r"\bkg\b", 0.001), (r"\b(?:t|to|tons?|tonnes?|Tonnen|mt)\b", 1.0)],
    "price_eur_t": [(r"/\s*kg\b", 1000.0), (r"/\s*(?:t|to|mt)\b", 1.0)],
}


def read_field(field: str, quote: str) -> Reading:
    """Read one numeric field from the text the model quoted for it."""
    if field in ("thickness_mm", "width_mm", "length_mm"):
        d = dims(quote)
        if d and field in d:
            return Reading(float(d[field]), False)
    tokens = _NUM_RE.findall(quote)
    if not tokens:
        return Reading(None, False, "no number in the quoted text")
    scale = 1.0
    for pattern, factor in _UNIT_HINTS.get(field, []):
        if re.search(pattern, quote, flags=re.IGNORECASE):
            scale = factor
            break
    candidates: list[float] = []
    for tok in tokens:
        for v in readings(tok):
            v = round(v * scale, 6)
            if plausible(field, v) and v not in candidates:
                candidates.append(v)
    if not candidates:
        return Reading(None, False, "no plausible value in the quoted text")
    if len(candidates) > 1:
        return Reading(None, True, "quoted text holds more than one plausible value: " + ", ".join(f"{c:g}" for c in candidates))
    return Reading(candidates[0], False)
