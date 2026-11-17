"""Exports a simulated desk for the console's demo mode: console/public/demo.json.

The RFQs, extractions, offers and checks are real outputs of the platform on a simulated
month (seed 11, first two days); the evaluation tables come from reports/.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import timedelta
from pathlib import Path

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from steelmatch.desk import Desk  # noqa: E402
from steelmatch.domain.grades import GradeKB  # noqa: E402
from steelmatch.llm.gateway import Gateway  # noqa: E402
from steelmatch.search.inventory import MemoryInventory  # noqa: E402
from steelmatch.service.runtime import result_json  # noqa: E402
from steelmatch.settings import load  # noqa: E402

from .harness import build, inbound, install_sim  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


async def export(seed: int = 11, days: int = 2, limit: int = 36) -> dict:
    w = build(seed)
    install_sim(w, "demo")
    settings = load()
    gw = Gateway.from_file()
    desk = Desk(settings, gw, GradeKB.load(), MemoryInventory(rrf_k=settings.rrf_k))
    lists = []
    for s in w.lists[0]:
        res = await desk.import_stocklist(s.supplier_id, s.country, s.filename, s.data, s.filename.rsplit(".", 1)[0], w.month.today)
        lists.append({"supplier_id": s.supplier_id, "filename": s.filename, "status": res.status, "items": len(res.items),
                      "used_model": res.used_model, "problems": res.problems})
    rfqs = []
    for r in [x for x in w.month.rfqs if x.day < days][:limit]:
        msg = w.docs[r.message_id].message
        today = w.month.today + timedelta(days=r.day)
        result = await desk.process(inbound(msg), today)
        ids = [f.item_id for l in result.lines for f in l.offers + l.review_offers]
        items = {i: desk.inventory.get(i) for i in ids}
        rfqs.append({"message_id": r.message_id, "received": msg["received"], "sender": msg["sender"], "subject": msg["subject"],
                     "status": result.status, "body": msg["body"], "attachments": [a["filename"] for a in msg["attachments"]],
                     "result": result_json(result, {k: v for k, v in items.items() if v})})
    reports = {}
    for name, rel in {"dev": "reports/dev.json", "heldout": "reports/heldout.json", "bakeoff": "reports/bakeoff.json",
                      "gate": "evals/baseline.json"}.items():
        p = ROOT / rel
        if p.exists():
            data = json.loads(p.read_text())
            if name == "gate":
                data = {k: v for k, v in data.items() if k != "results"}
            if name in ("dev", "heldout"):
                data = {"seeds": data["seeds"], "mean": data["mean"]}
            reports[name] = data
    out = {"generated_from": f"seed {seed}, days 0-{days - 1}", "today": w.month.today.isoformat(), "rfqs": rfqs, "lists": lists,
           "gateway": gw.summary(), "reports": reports,
           "mcp": {"tools": ["search_inventory", "get_lots", "resolve_grade", "check_lots", "get_rfq", "record_correction"],
                   "clients": [{"client_id": "trader-desk", "scopes": ["inventory:read", "inventory:write", "knowledge:read", "rfq:read", "rfq:match", "rfq:review"]},
                               {"client_id": "erp-connector", "scopes": ["rfq:read"]},
                               {"client_id": "catalogue-bot", "scopes": ["inventory:read", "knowledge:read"]}]}}
    path = ROOT / "console" / "public" / "demo.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return out


if __name__ == "__main__":
    o = asyncio.run(export())
    print(len(o["rfqs"]), "rfqs exported")
