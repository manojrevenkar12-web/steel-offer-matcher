"""Builds the eval set from a dev month: every email a trader had to correct, with the confirmed values.

python -m steeleval.evalset build     # write evals/cases from dev seed corrections
python -m steeleval.evalset gate      # run the gate with the current prompt and route
python -m steeleval.evalset accept    # run the gate and, if it passes, make it the baseline
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from steelmatch.agents import extractor as ex  # noqa: E402
from steelmatch.docs.reader import read  # noqa: E402
from steelmatch.domain.grades import GradeKB  # noqa: E402
from steelmatch.llm.bridge import gateway_model  # noqa: E402
from steelmatch.llm.gateway import Gateway  # noqa: E402
from steelmatch.observe.langfuse_bridge import local_prompt  # noqa: E402
from steelmatch.review import gate  # noqa: E402
from steelmatch.settings import load  # noqa: E402

from .harness import build, inbound, install_sim  # noqa: E402

GATE_SEED = 23


def expected(rfq) -> dict:
    from steelworld.catalog import BY_ID

    return {"lines": [{"line": i, "grade_id": l.material, "form": l.form, "thickness_mm": l.thickness_mm, "width_mm": l.width_mm,
                       "length_mm": l.length_mm, "quantity_t": l.quantity_t, "finish": BY_ID[l.material].finish}
                      for i, l in enumerate(rfq.lines, start=1)]}


async def build_cases(limit: int = 60) -> int:
    """Cases are the emails the platform sent to review on the gate seed: what traders corrected."""
    w = build(GATE_SEED)
    install_sim(w, "evalset")
    settings, kb = load(), GradeKB.load()
    agent = ex.build_agent(gateway_model(Gateway.from_file(), settings.routes["extract"], "extractor"), local_prompt("extractor").text)
    n = 0
    for rfq in w.month.rfqs:
        if not rfq.is_rfq or rfq.fmt == "image":
            continue
        msg = w.docs[rfq.message_id].message
        doc = read(inbound(msg))
        try:
            claims = (await agent.run(doc.render(), deps=ex.ExtractDeps(kb))).output
        except Exception:
            continue
        decided = ex.decide(claims, doc, kb, settings.required_fields, w.month.today)
        if decided.status == "review":
            gate.write_case(f"g{GATE_SEED}-{rfq.message_id.split('-', 1)[1]}", msg, expected(rfq), "trader correction")
            n += 1
        if n >= limit:
            break
    return n


async def run_gate(route: str | None = None) -> gate.GateReport:
    w = build(GATE_SEED)
    install_sim(w, "gate")
    settings, kb = load(), GradeKB.load()
    agent = ex.build_agent(gateway_model(Gateway.from_file(), route or settings.routes["extract_hard"], "extractor"),
                           local_prompt("extractor").text)

    async def extract(text: str):
        try:
            return (await agent.run(text, deps=ex.ExtractDeps(kb))).output
        except Exception:
            return None

    return await gate.run(extract, kb, settings.required_fields, w.month.today)


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "gate"
    if cmd == "build":
        print(asyncio.run(build_cases()), "cases")
    elif cmd in ("gate", "accept"):
        report = asyncio.run(run_gate(sys.argv[2] if len(sys.argv) > 2 else None))
        print(json.dumps({k: v for k, v in report.to_json().items() if k != "results"}, indent=1))
        if cmd == "accept":
            gate.accept(report)
        if not report.passed:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
