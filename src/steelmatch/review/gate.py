"""The evaluation gate: no prompt, model or rule change ships without a replay.

Eval cases are emails a person corrected, with the values the person confirmed. The gate runs
the current extraction over every case and compares with the accepted baseline. A change
passes only when no case that was decided correctly before is now decided wrongly, and no
case gains a wrongly decided value. The baseline moves only after a pass (`accept`).
"""

from __future__ import annotations

import base64
import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from ..agents import extractor as ex
from ..docs.reader import Attachment, Inbound, read
from ..domain.grades import GradeKB

ROOT = Path(__file__).resolve().parents[3]
CASES = ROOT / "evals" / "cases"
BASELINE = ROOT / "evals" / "baseline.json"
KEYS = ("grade_id", "form", "thickness_mm", "width_mm", "length_mm", "quantity_t")


@dataclass
class CaseResult:
    case_id: str
    exact: bool
    wrong_values: list[str]
    review: bool


@dataclass
class GateReport:
    passed: bool
    cases: int
    exact: int
    wrong_values: int
    regressions: list[str] = field(default_factory=list)
    results: list[CaseResult] = field(default_factory=list)

    def to_json(self) -> dict:
        return asdict(self)


def case_to_inbound(case: dict) -> Inbound:
    m = case["input"]
    return Inbound(message_id=case["case_id"], sender=m["sender"], subject=m["subject"], body=m["body"], received=m["received"],
                   attachments=[Attachment(filename=a["filename"], content_type=a["content_type"], data=base64.b64decode(a["data_b64"]))
                                for a in m["attachments"]])


def write_case(case_id: str, msg: dict, expected: dict, source: str) -> Path:
    CASES.mkdir(parents=True, exist_ok=True)
    payload = {"case_id": case_id, "source": source, "expected": expected,
               "input": {k: msg[k] for k in ("sender", "subject", "body", "received")} | {
                   "attachments": [{"filename": a["filename"], "content_type": a["content_type"],
                                    "data_b64": base64.b64encode(a["data"]).decode()} for a in msg["attachments"]]}}
    p = CASES / f"{case_id}.json"
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True))
    return p


def load_cases() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(CASES.glob("*.json"))]


def judge(case: dict, extraction: ex.Extraction) -> CaseResult:
    wrong: list[str] = []
    exact = True
    got = {l.line: l for l in extraction.lines}
    for exp in case["expected"]["lines"]:
        d = got.get(exp["line"])
        if d is None:
            exact = False
            continue
        for k in KEYS:
            want, have = exp.get(k), getattr(d, k)
            if want is None and have is None:
                continue
            if have is None:  # left open for a person: not exact, but not wrong
                exact = False
                continue
            if want is None or (isinstance(want, float | int) and abs(float(want) - float(have)) > 1e-6) \
                    or (isinstance(want, str) and want != have):
                wrong.append(f"{exp['line']}.{k}")
                exact = False
    return CaseResult(case["case_id"], exact and not extraction.review, wrong, extraction.status == "review")


async def run(extract, kb: GradeKB, required: tuple[str, ...], today: date, cases: list[dict] | None = None) -> GateReport:
    """`extract(doc_text) -> RfqClaims | None` is the agent under test (any route, any prompt)."""
    cases = cases if cases is not None else load_cases()
    results = []
    for case in cases:
        doc = read(case_to_inbound(case))
        claims = await extract(doc.render())
        extraction = ex.decide(claims, doc, kb, required, today) if claims else ex.Extraction("review", [], ex.Policy())
        results.append(judge(case, extraction))
    baseline = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    before = {r["case_id"]: r for r in baseline.get("results", [])}
    regressions = []
    for r in results:
        b = before.get(r.case_id)
        if r.wrong_values and (b is None or len(r.wrong_values) > len(b["wrong_values"])):
            regressions.append(f"{r.case_id}: wrong {', '.join(r.wrong_values)}")
        elif b and b["exact"] and not r.exact:
            regressions.append(f"{r.case_id}: was exact, now goes to review")
    return GateReport(not regressions, len(results), sum(r.exact for r in results), sum(len(r.wrong_values) for r in results),
                      regressions, results)


def accept(report: GateReport) -> None:
    if not report.passed:
        raise ValueError("a failing gate cannot become the baseline")
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(json.dumps(report.to_json(), indent=1, sort_keys=True))
