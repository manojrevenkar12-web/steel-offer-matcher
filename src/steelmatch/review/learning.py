"""Corrections become eval cases, and repeated grade corrections become aliases after a replay.

When a trader resolves an unknown designation ("St37" means S235JR), the correction is kept.
Once the same designation has been corrected the same way often enough, it becomes a
candidate alias. The candidate is replayed against every stored case: it is accepted only when
no case that resolved correctly before resolves differently after. No rule without a replay.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from ..domain.grades import GradeKB, normalise


@dataclass
class GradeCase:
    case_id: str
    designation: str
    expected: str  # grade id a person confirmed


@dataclass
class AliasDecision:
    alias: str
    grade_id: str
    accepted: bool
    regressions: list[str]
    supporting: int


@dataclass
class AliasLearner:
    min_corrections: int = 2
    corrections: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    cases: list[GradeCase] = field(default_factory=list)
    decisions: list[AliasDecision] = field(default_factory=list)

    def record(self, case_id: str, designation: str, grade_id: str) -> None:
        self.corrections[normalise(designation)][grade_id] += 1
        self.cases.append(GradeCase(case_id, designation, grade_id))

    def confirm(self, case_id: str, designation: str, grade_id: str) -> None:
        """A designation a person saw resolve correctly; kept as a replay case."""
        self.cases.append(GradeCase(case_id, designation, grade_id))

    def candidates(self, kb: GradeKB) -> list[tuple[str, str, int]]:
        out = []
        for alias, votes in self.corrections.items():
            if alias in kb.learned or kb.resolve(alias).grade_id:
                continue
            (gid, n), *rest = votes.most_common()
            if n >= self.min_corrections and (not rest or rest[0][1] < n):
                out.append((alias, gid, n))
        return out

    def replay(self, kb: GradeKB, alias: str, grade_id: str) -> list[str]:
        trial = kb.with_learned(alias, grade_id)
        regressions = []
        for c in self.cases:
            before = kb.resolve(c.designation).grade_id
            after = trial.resolve(c.designation).grade_id
            if before == c.expected and after != c.expected:
                regressions.append(c.case_id)
            elif after is not None and after != c.expected:
                regressions.append(c.case_id)
        return regressions

    def learn(self, kb: GradeKB) -> GradeKB:
        for alias, gid, n in self.candidates(kb):
            regressions = self.replay(kb, alias, gid)
            ok = not regressions
            self.decisions.append(AliasDecision(alias, gid, ok, regressions, n))
            if ok:
                kb = kb.with_learned(alias, gid)
        return kb
