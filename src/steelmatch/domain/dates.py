"""Delivery dates as traders write them: "KW 50", "bis 15.12.", "15.12.2026", "ASAP"."""

from __future__ import annotations

import re
from datetime import date, timedelta


def delivery_by(quote: str, today: date) -> tuple[date | None, bool]:
    """Returns (date, understood). 'ASAP' and 'sofort' are understood and mean no deadline."""
    q = quote.strip()
    if re.search(r"\b(asap|sofort|umgehend|ab lager|prompt)\b", q, re.IGNORECASE):
        return None, True
    m = re.search(r"\b(?:KW|CW|week|Woche)\s*\.?\s*(\d{1,2})(?:\s*/\s*(\d{2,4}))?", q, re.IGNORECASE)
    if m:
        week = int(m.group(1))
        year = _year(m.group(2), today)
        if not 1 <= week <= 53:
            return None, False
        if m.group(2) is None and week < today.isocalendar().week:
            year += 1
        try:
            return date.fromisocalendar(year, week, 5), True
        except ValueError:
            return None, False
    m = re.search(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2,4})?", q)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        year = _year(m.group(3), today)
        try:
            out = date(year, mo, d)
        except ValueError:
            return None, False
        if m.group(3) is None and out < today - timedelta(days=30):
            out = date(year + 1, mo, d)
        return out, True
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", q)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))), True
        except ValueError:
            return None, False
    return None, False


def _year(raw: str | None, today: date) -> int:
    if not raw:
        return today.year
    y = int(raw)
    return 2000 + y if y < 100 else y
