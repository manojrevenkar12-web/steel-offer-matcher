"""Evaluation entry points.

python -m steeleval.evaluate dev       # dev months, every design -> reports/dev.json
python -m steeleval.evaluate bakeoff   # extraction models compared -> reports/bakeoff.json
python -m steeleval.evaluate lock      # fingerprint the platform before the held-out run
python -m steeleval.evaluate heldout   # held-out months, once -> reports/heldout.json
python -m steeleval.evaluate readme    # write the results block in README.md
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import statistics
import sys
import time
from datetime import timedelta
from pathlib import Path

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from steelmatch.agents import extractor as ex  # noqa: E402
from steelmatch.docs.reader import read  # noqa: E402
from steelmatch.domain.grades import GradeKB  # noqa: E402
from steelmatch.llm.bridge import gateway_model  # noqa: E402
from steelmatch.llm.gateway import Gateway  # noqa: E402
from steelmatch.observe.langfuse_bridge import local_prompt  # noqa: E402
from steelmatch.settings import load as load_settings  # noqa: E402

from .harness import World, build, inbound, install_sim, run_manual, run_model_decides, run_platform  # noqa: E402
from .scoring import score, truth_table  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "reports"
LOCK = ROOT / "config" / "heldout.lock"
DESIGNS = ["manual", "model_decides", "platform", "platform_no_agent"]
LOCKED = ["src/steelmatch", "config/matcher.json", "config/models.json", "prompts", "knowledge"]


def world_cfg() -> dict:
    return json.loads((ROOT / "config" / "world.json").read_text())


async def run_seed(seed: int, heldout: bool = False) -> dict:
    w = build(seed, heldout)
    table = truth_table(w)
    out = {}
    for design in DESIGNS:
        t = time.time()
        if design == "manual":
            log = run_manual(w)
        elif design == "model_decides":
            log = await run_model_decides(w)
        elif design == "platform":
            log = await run_platform(w)
        else:
            log = await run_platform(w, use_agent=False, label="platform_no_agent")
        out[design] = score(w, log, table) | {"gateway": log.gateway, "seconds": round(time.time() - t, 1)}
        print(f"seed {seed} {design}: coverage {out[design]['coverage_pct']}%, wrong {out[design]['wrong_offers']}", file=sys.stderr)
    return out


def mean_of(runs: list[dict]) -> dict:
    out: dict = {}
    for design in DESIGNS:
        rows = [r[design] for r in runs]
        agg = {}
        for k, v in rows[0].items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                agg[k] = round(statistics.mean(r[k] for r in rows), 4)
        out[design] = agg
    return out


async def dev() -> dict:
    cfg = world_cfg()
    runs = {}
    for seed in cfg["dev_seeds"]:
        runs[str(seed)] = await run_seed(seed)
    report = {"seeds": cfg["dev_seeds"], "mean": mean_of(list(runs.values())), "runs": runs}
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "dev.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, sort_keys=True))
    return report


# Model bake-off ------------------------------------------------------------------------------
async def bakeoff(seed: int | None = None, n: int = 160) -> dict:
    cfg = world_cfg()
    seed = seed or cfg["dev_seeds"][0]
    w = build(seed)
    install_sim(w, "bakeoff")
    settings = load_settings()
    kb = GradeKB.load()
    base = json.loads((ROOT / "config" / "models.json").read_text())
    rfqs = [r for r in w.month.rfqs if r.is_rfq and r.fmt != "image"][:n]
    results = {}
    for model in ["sim/fast-commercial", "sim/strong-commercial", "sim/open-weights-8b"]:
        conf = json.loads(json.dumps(base))
        price = next(d for d in conf["deployments"] if d["model"] == model)
        conf["deployments"] = [{"route": "extract", "model": model, "price_in": price["price_in"], "price_out": price["price_out"]}]
        conf["fallbacks"] = {}
        gw = Gateway(conf)
        agent = ex.build_agent(gateway_model(gw, "extract", "extractor"), local_prompt("extractor").text)
        lines = exact = wrong_decided = review_rfqs = raw_wrong = 0
        for rfq in rfqs:
            doc_msg = w.docs[rfq.message_id].message
            doc = read(inbound(doc_msg))
            today = w.month.today + timedelta(days=rfq.day)
            try:
                claims = (await agent.run(doc.render(), deps=ex.ExtractDeps(kb))).output
            except Exception:
                review_rfqs += 1
                continue
            decided = ex.decide(claims, doc, kb, settings.required_fields, today)
            review_rfqs += 1 if decided.status != "ready" else 0
            by_line = {l.line: l for l in decided.lines}
            for i, tl in enumerate(rfq.lines, start=1):
                lines += 1
                d = by_line.get(i)
                if d is not None:
                    same = (d.grade_id == tl.material and abs(d.thickness_mm - tl.thickness_mm) < 1e-6
                            and abs(d.width_mm - tl.width_mm) < 1e-6 and abs(d.quantity_t - tl.quantity_t) < 1e-6 and d.form == tl.form)
                    exact += 1 if same else 0
                    wrong_decided += 0 if same else 1
                rc = claims.lines[i - 1] if i - 1 < len(claims.lines) else None
                if rc is None or rc.thickness_mm != tl.thickness_mm or rc.width_mm != tl.width_mm or rc.quantity_t != tl.quantity_t:
                    raw_wrong += 1
        s = gw.summary().get(model, {"cost_eur": 0, "p50_ms": 0, "p95_ms": 0, "calls": 0})
        results[model.split("/", 1)[1]] = {
            "rfqs": len(rfqs), "lines": lines, "lines_decided_exactly": exact,
            "lines_decided_wrongly": wrong_decided, "raw_model_lines_wrong": raw_wrong,
            "rfqs_sent_to_review": review_rfqs, "cost_per_rfq_eur": round(s["cost_eur"] / max(1, len(rfqs)), 5),
            "p50_ms": round(s["p50_ms"]), "p95_ms": round(s["p95_ms"]),
        }
    report = {"seed": seed, "note": "Simulated model profiles (config/world.json), not measurements of named commercial models.",
              "models": results}
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "bakeoff.json").write_text(json.dumps(report, indent=1, sort_keys=True))
    return report


# Held-out --------------------------------------------------------------------------------------
def fingerprint() -> str:
    h = hashlib.sha256()
    for rel in LOCKED:
        p = ROOT / rel
        files = sorted(x for x in (p.rglob("*") if p.is_dir() else [p]) if x.is_file() and "__pycache__" not in x.parts)
        for f in files:
            h.update(str(f.relative_to(ROOT)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()


def write_lock() -> str:
    fp = fingerprint()
    LOCK.write_text(fp + "\n")
    return fp


async def heldout() -> dict:
    if (REPORTS / "heldout.json").exists():
        raise SystemExit("The held-out months have been run. They run once; see reports/heldout.json.")
    if not LOCK.exists() or LOCK.read_text().strip() != fingerprint():
        raise SystemExit("The platform changed since `lock`. Lock first, then run the held-out months once.")
    cfg = world_cfg()
    runs = {str(s): await run_seed(s, heldout=True) for s in cfg["heldout_seeds"]}
    report = {"seeds": cfg["heldout_seeds"], "fingerprint": fingerprint(), "mean": mean_of(list(runs.values())), "runs": runs}
    (REPORTS / "heldout.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, sort_keys=True))
    return report


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dev"
    if cmd == "dev":
        asyncio.run(dev())
    elif cmd == "bakeoff":
        asyncio.run(bakeoff())
    elif cmd == "lock":
        print(write_lock())
    elif cmd == "heldout":
        asyncio.run(heldout())
    elif cmd == "readme":
        from .readme import write

        write()
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
