"""Runs the service against a simulated month, for demos and the smoke test.

python -m steeleval.serve            # http://127.0.0.1:8000, MCP at /mcp/
POST /sim/replay?day=0               # loads that day's stock lists and pushes its emails through Pub/Sub
"""

from __future__ import annotations

import base64
import json
import os

import uvicorn
from fastapi import HTTPException

from steelmatch.service.app import create_app
from steelmatch.service.runtime import Runtime

from .harness import LIST_DAYS, build, install_sim


def app():
    world = build(int(os.environ.get("STEEL_SIM_SEED", "11")))
    install_sim(world, "serve")
    rt = Runtime.create()
    rt.today_override = world.month.today
    api = create_app(rt)

    @api.post("/sim/replay")
    async def replay(day: int = 0) -> dict:
        if not 0 <= day < world.month.days:
            raise HTTPException(400, "no such day")
        lists = 0
        if day in LIST_DAYS:
            for s in world.lists[day]:
                await rt.import_list(s.supplier_id, s.country, s.filename, s.data)
                lists += 1
        queued = 0
        for r in world.month.rfqs:
            if r.day == day:
                m = world.docs[r.message_id].message
                from steelmatch.docs.reader import Attachment, Inbound

                queued += await rt.enqueue_inbound(Inbound(**{k: m[k] for k in ("message_id", "sender", "subject", "body", "received")},
                                                           attachments=[Attachment(**a) for a in m["attachments"]]))
        return {"lists": lists, "queued": queued}

    return api


if __name__ == "__main__":
    uvicorn.run(app(), host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")))
