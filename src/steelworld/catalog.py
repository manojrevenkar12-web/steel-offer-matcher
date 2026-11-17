"""The world's materials: what each grade really is and every way people write it.

This is the world's own truth, written independently of the platform's knowledge base. Some
spellings here are ones a fresh knowledge base does not know ('kb_unknown'), because real
supplier lists and buyer emails contain them.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Material:
    id: str
    family: str
    finish: str
    rank: int  # within family, higher can replace lower when a buyer allows upgrades
    number: str
    legacy: tuple[str, ...] = ()
    suffix: tuple[str, ...] = ()
    kb_unknown: tuple[str, ...] = ()
    thickness_range: tuple[float, float] = (1.5, 20.0)
    base_price: float = 650.0
    coatings: tuple[str, ...] = ()
    surfaces: tuple[str, ...] = ()
    replaces: tuple[str, ...] = ()  # explicit upgrade list (stainless)


MATERIALS: list[Material] = [
    Material("S235JR", "structural", "hot_rolled", 10, "1.0038", ("St37-2", "RSt37-2"), ("S235JR+AR", "S235JR+N"),
             ("St37", "ST 37", "S235"), (1.5, 25.0), 640),
    Material("S235J2", "structural", "hot_rolled", 12, "1.0117", ("St37-3N",), ("S235J2+N",), ("S235 J2G3",), (2.0, 25.0), 665),
    Material("S275JR", "structural", "hot_rolled", 20, "1.0044", ("St44-2",), ("S275JR+AR",), ("S275",), (2.0, 25.0), 660),
    Material("S355JR", "structural", "hot_rolled", 30, "1.0045", (), ("S355JR+AR",), ("S355 JR AR",), (2.0, 25.0), 690),
    Material("S355J2", "structural", "hot_rolled", 32, "1.0577", ("St52-3", "St52-3N"), ("S355J2+N", "S355J2+AR"),
             ("St52", "ST 52-3 N", "S355"), (2.0, 30.0), 705),
    Material("S355K2", "structural", "hot_rolled", 33, "1.0596", (), ("S355K2+N",), (), (3.0, 30.0), 720),
    Material("DD11", "hr_forming", "hot_rolled", 10, "1.0332", ("StW22",), ("DD11+AR",), (), (1.5, 8.0), 630),
    Material("DD13", "hr_forming", "hot_rolled", 12, "1.0335", ("StW24",), (), ("DD 13 P&O",), (1.5, 8.0), 650),
    Material("DC01", "cr_forming", "cold_rolled", 10, "1.0330", ("St12",), ("DC01+A",), ("DC01-AM", "St 12-03"), (0.4, 3.0), 720),
    Material("DC04", "cr_forming", "cold_rolled", 12, "1.0338", ("St14",), ("DC04+A",), ("DC04 AM",), (0.4, 3.0), 750),
    Material("DX51D", "coated_forming", "coated", 10, "1.0917", ("St02Z",), (), ("DX51", "DX 51 D"), (0.4, 3.0), 790,
             coatings=("Z100", "Z140", "Z200", "Z275")),
    Material("DX53D", "coated_forming", "coated", 12, "1.0951", ("St05Z",), (), (), (0.4, 2.5), 820, coatings=("Z140", "Z275")),
    Material("S350GD", "coated_structural", "coated", 13, "1.0529", ("StE350-3Z",), (), ("S350 GD",), (0.6, 3.0), 840,
             coatings=("Z275", "ZM120")),
    Material("1.4301", "stainless_austenitic", "stainless", 10, "1.4301", ("X5CrNi18-10", "AISI 304"), (), ("V2A 304", "304"),
             (0.5, 6.0), 2650, surfaces=("2B", "1D", "BA")),
    Material("1.4404", "stainless_austenitic", "stainless", 21, "1.4404", ("X2CrNiMo17-12-2", "AISI 316L"), (), ("316L", "V4A 316L"),
             (0.5, 6.0), 3550, surfaces=("2B", "1D"), replaces=("1.4301",)),
    Material("1.4016", "stainless_ferritic", "stainless", 10, "1.4016", ("X6Cr17", "AISI 430"), (), ("430",), (0.5, 3.0), 1650,
             surfaces=("2B", "BA")),
]

BY_ID = {m.id: m for m in MATERIALS}


def can_replace(requested: str, offered: str) -> bool:
    """World truth: may `offered` stand in for `requested` when the buyer allows a higher grade?"""
    r, o = BY_ID[requested], BY_ID[offered]
    if o.replaces:
        return requested in o.replaces
    if r.family != o.family or r.family.startswith("stainless"):
        return False
    return o.rank > r.rank


def designation(m: Material, style: str, pick) -> str:
    if style == "number":
        return m.number
    if style == "legacy" and m.legacy:
        return pick(m.legacy)
    if style == "suffix" and m.suffix:
        return pick(m.suffix)
    if style == "kb_unknown" and m.kb_unknown:
        return pick(m.kb_unknown)
    return m.id
