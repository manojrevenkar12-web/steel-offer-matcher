"""FastAPI service: Pub/Sub inbound, stock-list upload, the review API for the console, and MCP.

Run: uvicorn steelmatch.service.app:app   (after `alembic upgrade head`)
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ..docs.reader import Attachment, Inbound
from . import mcp_server
from .auth import Client, lookup
from .runtime import Runtime

ROOT = Path(__file__).resolve().parents[3]


def caller(scope: str):
    def dep(authorization: Annotated[str | None, Header()] = None) -> Client:
        token = (authorization or "").removeprefix("Bearer ").strip()
        c = lookup(token)
        if c is None:
            raise HTTPException(401, "missing or unknown token")
        if scope not in c.scopes:
            raise HTTPException(403, f"needs scope {scope}")
        return c

    return dep


class PubSubMessage(BaseModel):
    data: str
    messageId: str | None = None
    attributes: dict[str, str] | None = None


class PubSubPush(BaseModel):
    message: PubSubMessage
    subscription: str | None = None


class Correction(BaseModel):
    line: int
    field: str
    value: str


def create_app(rt: Runtime | None = None, worker: bool | None = None) -> FastAPI:
    hosts = os.environ.get("STEEL_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",")
    runtime = rt or Runtime.create()
    mcp = mcp_server.build(runtime)
    run_worker = worker if worker is not None else os.environ.get("STEEL_WORKER", "1") == "1"

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        await runtime.startup()
        stop = asyncio.Event()
        task = None
        if run_worker:
            async def loop():
                while not stop.is_set():
                    if not await runtime.run_once():
                        with contextlib.suppress(asyncio.TimeoutError):
                            await asyncio.wait_for(stop.wait(), 1.0)
            task = asyncio.create_task(loop())
        async with mcp.session_manager.run():
            yield
        stop.set()
        if task:
            await task
        runtime.observer.flush()
        await runtime.engine.dispose()

    app = FastAPI(title="steel-offer-matcher", lifespan=lifespan)
    app.state.runtime = runtime
    app.add_middleware(CORSMiddleware, allow_origins=os.environ.get("STEEL_CONSOLE_ORIGINS", "http://localhost:5173").split(","),
                       allow_methods=["*"], allow_headers=["*"])

    @app.get("/healthz")
    async def health() -> dict:
        return {"ok": True}

    @app.post("/pubsub/inbound", status_code=204)
    async def inbound(push: PubSubPush) -> None:
        """Gmail/IMAP bridge publishes each email as JSON to Pub/Sub; this is the push endpoint."""
        try:
            raw = json.loads(base64.b64decode(push.message.data))
            msg = Inbound(**{k: raw[k] for k in ("message_id", "sender", "subject", "body", "received")},
                          attachments=[Attachment(filename=a["filename"], content_type=a["content_type"],
                                                  data=base64.b64decode(a["data_b64"])) for a in raw.get("attachments", [])])
        except Exception as e:  # malformed push: acknowledge so Pub/Sub does not retry forever, but say why
            raise HTTPException(400, f"unreadable message: {e}") from e
        await runtime.enqueue_inbound(msg)

    @app.post("/api/stocklists")
    async def upload(supplier_id: Annotated[str, Form()], country: Annotated[str, Form()], file: UploadFile = File(...),
                     _: Client = Depends(caller("inventory:write"))) -> dict:
        row = await runtime.import_list(supplier_id, country.upper(), file.filename or "list.csv", await file.read())
        return {"list_id": row.list_id, "status": row.status, "items": row.items, "skipped": row.skipped, "problems": row.problems}

    @app.get("/api/overview")
    async def overview(_: Client = Depends(caller("rfq:read"))) -> dict:
        return await runtime.overview()

    @app.get("/api/rfqs")
    async def rfqs(status: str | None = None, _: Client = Depends(caller("rfq:read"))) -> list[dict]:
        return await runtime.list_rfqs(status)

    @app.get("/api/rfqs/{message_id}")
    async def rfq(message_id: str, _: Client = Depends(caller("rfq:read"))) -> dict:
        r = await runtime.get_rfq(message_id)
        if r is None:
            raise HTTPException(404, "no such RFQ")
        return r

    @app.post("/api/rfqs/{message_id}/corrections")
    async def correct(message_id: str, c: Correction, who: Client = Depends(caller("rfq:review"))) -> dict:
        try:
            return await runtime.correct(message_id, c.line, c.field, c.value, who.client_id)
        except KeyError as e:
            raise HTTPException(404, "no such RFQ, or not processed yet") from e

    @app.post("/api/rfqs/{message_id}/approve", status_code=204)
    async def approve(message_id: str, who: Client = Depends(caller("rfq:review"))) -> None:
        try:
            await runtime.approve(message_id, who.client_id)
        except KeyError as e:
            raise HTTPException(404, "no such RFQ") from e

    @app.post("/api/worker/drain")
    async def drain(_: Client = Depends(caller("rfq:match"))) -> dict:
        return {"processed": await runtime.drain()}

    @app.get("/api/reports/{name}")
    async def report(name: str, _: Client = Depends(caller("rfq:read"))) -> dict:
        allowed = {"dev": "reports/dev.json", "heldout": "reports/heldout.json", "bakeoff": "reports/bakeoff.json",
                   "gate": "evals/baseline.json"}
        if name not in allowed or not (ROOT / allowed[name]).exists():
            raise HTTPException(404, "no such report")
        return json.loads((ROOT / allowed[name]).read_text())

    app.mount("/mcp", mcp_server.asgi(mcp, hosts))
    return app


def __getattr__(name: str):  # `uvicorn steelmatch.service.app:app` builds the app lazily
    if name == "app":
        return create_app()
    raise AttributeError(name)
