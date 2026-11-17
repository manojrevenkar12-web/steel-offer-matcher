"""Steel grade resolution: identifiers decide, names propose.

A designation resolves to a canonical grade only through an exact, normalised match on a known
identifier: the EN name, the material number or a superseded DIN name listed as an alias.
Anything else (a typo, a designation from another standard, a trader's shorthand) yields
proposals ranked by similarity, and proposals never decide: they go to a person.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

# Delivery-condition and coating suffixes that ride on a designation but are not the grade.
_CONDITION = re.compile(r"\+(?:AR|N|M|C|CR|LC|A|Q|QT)\b", re.IGNORECASE)
_COATING = re.compile(r"\+?\b(Z|ZF|ZM|AZ|AS)\s?(\d{2,3})\b", re.IGNORECASE)
_FINISH = re.compile(r"\b(?:1D|2B|2D|2R|2G|2J|BA)\b", re.IGNORECASE)


def normalise(text: str) -> str:
    t = text.upper().replace("WERKSTOFF", "").replace("W.-NR.", "").replace("W.NR.", "").replace("WNR", "")
    t = re.sub(r"[\s\-_/]", "", t)
    return t.strip(".:,;")


@dataclass(frozen=True)
class Grade:
    id: str
    number: str
    family: str
    level: int
    yield_mpa: int | None = None
    impact_c: int | None = None
    cev_max: float | None = None
    aliases: tuple[str, ...] = ()
    comparable: tuple[str, ...] = ()
    may_replace: tuple[str, ...] = ()


@dataclass(frozen=True)
class Resolution:
    grade_id: str | None
    via: str  # "id" | "number" | "alias" | "learned" | "comparable" | "none"
    coating: str | None = None
    proposals: tuple[str, ...] = ()
    note: str = ""


@dataclass
class GradeKB:
    grades: dict[str, Grade]
    learned: dict[str, str] = field(default_factory=dict)  # normalised alias -> grade id, accepted after replay

    @classmethod
    def load(cls, path: Path | None = None, learned: dict[str, str] | None = None) -> "GradeKB":
        data = json.loads((path or ROOT / "knowledge" / "grades.json").read_text())
        grades = {}
        for g in data["grades"]:
            grades[g["id"]] = Grade(
                id=g["id"], number=g["number"], family=g["family"], level=g["level"],
                yield_mpa=g.get("yield_mpa"), impact_c=g.get("impact_c"), cev_max=g.get("cev_max"),
                aliases=tuple(g.get("aliases", [])), comparable=tuple(g.get("comparable", [])),
                may_replace=tuple(g.get("may_replace", [])),
            )
        return cls(grades=grades, learned=dict(learned or {}))

    def with_learned(self, alias: str, grade_id: str) -> "GradeKB":
        if grade_id not in self.grades:
            raise KeyError(grade_id)
        merged = dict(self.learned)
        merged[normalise(alias)] = grade_id
        return GradeKB(grades=self.grades, learned=merged)

    @property
    def _index(self) -> dict[str, tuple[str, str]]:
        return _build_index(tuple(self.grades.values()))

    def resolve(self, text: str) -> Resolution:
        if not text or not text.strip():
            return Resolution(None, "none", note="no designation given")
        coating = None
        cm = _COATING.search(text)
        if cm:
            coating = f"{cm.group(1).upper()}{cm.group(2)}"
            text = text[: cm.start()] + text[cm.end():]
        core = _FINISH.sub("", _CONDITION.sub("", text))
        key = normalise(core)
        hit = self._exact(key)
        if hit:
            return Resolution(hit[0], hit[1], coating)
        parts = [p for p in re.split(r"[/,;()]|\bbzw\.?|\bor\b|\boder\b", core) if p.strip()]
        if len(parts) > 1:
            hits = [self._exact(normalise(p)) for p in parts]
            ids = {h[0] for h in hits if h}
            if len(ids) == 1 and all(hits):
                return Resolution(ids.pop(), hits[0][1], coating)
            if len(ids) > 1:
                return Resolution(None, "none", coating, tuple(sorted(ids)), note="designation names more than one grade")
        for g in self.grades.values():
            if any(key in (normalise(c), normalise(c.replace("ASTM", ""))) for c in g.comparable):
                return Resolution(None, "comparable", coating, (g.id,),
                                  note=f"{core.strip()} is comparable to {g.id} but not the same material")
        return Resolution(None, "none", coating, self.propose(core), note=f"unknown designation {core.strip()!r}")

    def _exact(self, key: str) -> tuple[str, str] | None:
        hit = self._index.get(key)
        if hit:
            return hit
        if key in self.learned:
            return (self.learned[key], "learned")
        return None

    def propose(self, text: str, k: int = 3) -> tuple[str, ...]:
        key = normalise(text)
        scored = []
        for name, (gid, _) in self._index.items():
            scored.append((SequenceMatcher(None, key, name).ratio(), gid))
        scored.sort(reverse=True)
        out: list[str] = []
        for score, gid in scored:
            if score < 0.5:
                break
            if gid not in out:
                out.append(gid)
            if len(out) == k:
                break
        return tuple(out)

    def get(self, grade_id: str) -> Grade:
        return self.grades[grade_id]

    def upgrade_kind(self, requested: str, offered: str) -> str:
        """'same', 'upgrade' (offered may replace requested if the buyer allows), or 'no'."""
        if requested == offered:
            return "same"
        r, o = self.grades[requested], self.grades[offered]
        if o.may_replace:
            return "upgrade" if requested in o.may_replace else "no"
        if r.family != o.family or r.family.startswith("stainless"):
            return "no"
        if o.level <= r.level:
            return "no"
        if r.yield_mpa is not None and o.yield_mpa is not None and o.yield_mpa < r.yield_mpa:
            return "no"
        if r.impact_c is not None and o.impact_c is not None and o.impact_c > r.impact_c:
            return "no"
        return "upgrade"


@lru_cache(maxsize=8)
def _build_index(grades: tuple[Grade, ...]) -> dict[str, tuple[str, str]]:
    idx: dict[str, tuple[str, str]] = {}
    for g in grades:
        idx[normalise(g.id)] = (g.id, "id")
        idx[normalise(g.number)] = (g.id, "number")
        for a in g.aliases:
            idx.setdefault(normalise(a), (g.id, "alias"))
    return idx
