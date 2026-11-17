"""Shared data shapes: what a buyer asks for, what a supplier has, and what the buyer allows."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

Form = Literal["coil", "sheet", "plate", "strip"]
Finish = Literal["hot_rolled", "cold_rolled", "coated", "stainless"]
Quality = Literal["prime", "secondary"]
Certificate = Literal["none", "2.2", "3.1", "3.2"]

CERT_RANK = {"none": 0, "2.2": 1, "3.1": 2, "3.2": 3}


class Policy(BaseModel):
    """What the buyer allows beyond the literal spec. Defaults are the safe reading."""

    upgrades_ok: bool = False
    secondary_ok: bool = False
    certificate: Certificate = "none"
    coil_for_sheet_ok: bool = True  # a coil may be cut to length by the service centre
    slitting_ok: bool = True
    excluded_origins: list[str] = Field(default_factory=list)


class LineSpec(BaseModel):
    """One requested product, as decided by code from the quoted evidence."""

    line: int = 1
    form: Form
    finish: Finish
    grade_id: str
    grade_text: str = ""
    coating: str | None = None
    surface: str | None = None
    thickness_mm: float
    width_mm: float
    length_mm: float | None = None
    quantity_t: float
    max_price_eur_t: float | None = None
    delivery_country: str = "DE"
    delivery_by: date | None = None
    alternatives: list[dict] = Field(default_factory=list)  # e.g. {"thickness_mm": 2.5} or {"width_min_mm": 1000}


class StockItem(BaseModel):
    """One lot on a supplier's list, as decided by code from the list."""

    item_id: str
    supplier_id: str
    form: Form
    finish: Finish
    grade_id: str | None
    grade_text: str
    coating: str | None = None
    surface: str | None = None
    thickness_mm: float
    width_mm: float
    length_mm: float | None = None
    weight_t: float
    price_eur_t: float
    country: str
    origin: str | None = None
    available_from: date | None = None
    certificate: Certificate = "none"
    quality: Quality = "prime"
    edge: Literal["mill", "trimmed"] = "trimmed"

    def search_text(self) -> str:
        bits = [self.form, self.finish.replace("_", " "), self.grade_id or "", self.grade_text, self.coating or "",
                self.surface or "", f"{self.thickness_mm:g}x{self.width_mm:g}", self.quality, self.country]
        return " ".join(b for b in bits if b)
