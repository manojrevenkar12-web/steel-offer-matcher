"""The service on Postgres + pgvector: migrations, search contract, Pub/Sub intake, review loop, MCP with scopes.

Runs when STEEL_TEST_DATABASE_URL points at an empty database with the vector extension available,
e.g. postgresql+asyncpg://steel@127.0.0.1:5433/steeltest
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import socket
import threading
import time

import httpx
import pytest

URL = os.environ.get("STEEL_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs STEEL_TEST_DATABASE_URL")

TRADER = {"Authorization": "Bearer demo-trader-token"}
ERP = {"Authorization": "Bearer demo-erp-token"}


def migrate(url: str) -> None:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    sync = url.replace("+asyncpg", "+psycopg") if False else url
    cfg = Config("alembic.ini")
    cfg.attributes["url"] = sync

    async def reset():
        from sqlalchemy.ext.asyncio import create_async_engine

        eng = create_async_engine(url)
        async with eng.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        await eng.dispose()

    asyncio.run(reset())
    command.upgrade(cfg, "head")


@pytest.fixture(scope="module")
def world():
    from steeleval.harness import build, install_sim

    w = build(11)
    install_sim(w, "service-test")
    return w


@pytest.fixture(scope="module")
def server(world):
    import uvicorn

    from steelmatch.service.app import create_app
    from steelmatch.service.runtime import Runtime

    migrate(URL)
    os.environ["STEEL_ALLOWED_HOSTS"] = "localhost,127.0.0.1"
    rt = Runtime.create(URL)
    rt.today_override = world.month.today
    app = create_app(rt, worker=False)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(base + "/healthz").status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.05)
    yield base
    srv.should_exit = True
    t.join(timeout=10)


def push(base: str, msg: dict) -> httpx.Response:
    payload = {k: msg[k] for k in ("message_id", "sender", "subject", "body", "received")} | {
        "attachments": [{"filename": a["filename"], "content_type": a["content_type"], "data_b64": base64.b64encode(a["data"]).decode()}
                        for a in msg["attachments"]]}
    data = base64.b64encode(json.dumps(payload).encode()).decode()
    return httpx.post(base + "/pubsub/inbound", json={"message": {"data": data, "messageId": msg["message_id"]}})


def test_stock_lists_load_or_are_held_whole(server, world):
    for s in world.lists[0][:10]:
        r = httpx.post(server + "/api/stocklists", data={"supplier_id": s.supplier_id, "country": s.country},
                       files={"file": (s.filename, s.data)}, headers=TRADER, timeout=60)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] in ("loaded", "held")
        if body["status"] == "held":
            assert body["items"] == 0 and body["problems"]


def test_uploads_need_the_write_scope(server, world):
    s = world.lists[0][0]
    r = httpx.post(server + "/api/stocklists", data={"supplier_id": s.supplier_id, "country": s.country},
                   files={"file": (s.filename, s.data)}, headers=ERP)
    assert r.status_code == 403
    assert httpx.get(server + "/api/overview").status_code == 401


def test_pubsub_intake_is_idempotent_and_the_worker_processes(server, world):
    rfqs = [r for r in world.month.rfqs if r.day == 0][:8]
    for r in rfqs:
        assert push(server, world.docs[r.message_id].message).status_code == 204
    assert push(server, world.docs[rfqs[0].message_id].message).status_code == 204  # redelivery
    processed = httpx.post(server + "/api/worker/drain", headers=TRADER, timeout=300).json()["processed"]
    assert processed == len(rfqs)
    listed = httpx.get(server + "/api/rfqs", headers=TRADER).json()
    assert len(listed) == len(rfqs)
    assert {x["status"] for x in listed} <= {"ready", "review", "not_rfq"}
    one = next(x for x in listed if x["status"] in ("ready", "review"))
    detail = httpx.get(server + f"/api/rfqs/{one['message_id']}", headers=TRADER).json()
    assert detail["result"]["lines"] or detail["result"]["extraction"]["review"]
    assert detail["result"]["prompt_versions"]["extractor"].startswith("local-")


def test_corrections_become_eval_cases(server):
    listed = httpx.get(server + "/api/rfqs", headers=TRADER).json()
    target = next(x for x in listed if x["lines"] > 0)
    r = httpx.post(server + f"/api/rfqs/{target['message_id']}/corrections", json={"line": 1, "field": "thickness_mm", "value": "3.0"},
                   headers=TRADER)
    assert r.status_code == 200
    assert r.json()["expected"]["lines"][0]["thickness_mm"] == 3.0
    assert httpx.post(server + f"/api/rfqs/{target['message_id']}/corrections", json={"line": 1, "field": "x", "value": "1"},
                      headers=ERP).status_code == 403
    ov = httpx.get(server + "/api/overview", headers=TRADER).json()
    assert ov["eval_cases"] >= 1 and ov["model_cost_eur"] > 0 and ov["active_lots"] > 0


async def _mcp(base: str, token: str | None, calls: list[tuple[str, dict]]):
    import httpx2
    from mcp.client.client import Client
    from mcp.client.streamable_http import streamable_http_client

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    out = []
    async with httpx2.AsyncClient(headers=headers, timeout=30) as hc:
        async with Client(streamable_http_client(base + "/mcp/", http_client=hc)) as c:
            out.append(sorted(t.name for t in (await c.list_tools()).tools))
            for name, args in calls:
                out.append(await c.call_tool(name, args))
    return out


def test_mcp_tools_with_scopes(server):
    tools, found, grade, denied = asyncio.run(_mcp(server, "demo-readonly-token", [
        ("search_inventory", {"text": "S235JR 3x1500", "limit": 5}),
        ("resolve_grade", {"designation": "St 37-2"}),
        ("get_rfq", {"message_id": "nope"}),
    ]))
    assert {"search_inventory", "resolve_grade", "check_lots", "get_rfq", "record_correction"} <= set(tools)
    assert not found.is_error and json.loads(found.content[0].text)["lots"]
    assert json.loads(grade.content[0].text)["grade_id"] == "S235JR"
    assert denied.is_error and "rfq:read" in denied.content[0].text
    ov = httpx.get(server + "/api/overview", headers=TRADER).json()
    outcomes = {(m["tool"], m["outcome"]) for m in ov["mcp"]}
    assert ("search_inventory", "ok") in outcomes and ("get_rfq", "denied") in outcomes


def test_mcp_refuses_unknown_tokens(server):
    with pytest.raises(BaseException):
        asyncio.run(_mcp(server, "stolen-token", []))


async def test_postgres_search_keeps_the_in_memory_contract(world):
    """Same hard filters, same pool, exact designations first, in both implementations."""
    from steelmatch.agents import stocklist as sl
    from steelmatch.docs.reader import table_rows
    from steelmatch.domain.grades import GradeKB
    from steelmatch.search.inventory import MemoryInventory, SearchQuery
    from steelmatch.service.db import engine, sessions
    from steelmatch.service.search import PgInventory

    kb = GradeKB.load()
    s = next(x for x in world.lists[0] if x.mapping["columns"].get("thickness"))
    rows = table_rows(s.data, s.filename)
    h = sl.header_row(rows)
    items, _ = sl.read_rows(sl.ColumnMap(**s.mapping), rows[h], rows[h + 1:], s.supplier_id, s.country, kb, world.month.today, "contract")
    from sqlalchemy.dialects.postgresql import insert

    from steelmatch.service.db import StockListRow

    eng = engine(URL)
    sess = sessions(eng)
    pg = PgInventory(sess)
    await pg.clear()
    async with sess() as db:
        await db.execute(insert(StockListRow).values(list_id="contract", supplier_id=s.supplier_id, filename=s.filename,
                                                     status="loaded", problems=[]).on_conflict_do_nothing())
        await db.commit()
    await pg.add(items, "contract")
    mem = MemoryInventory()
    mem.add(items)
    queries = [
        SearchQuery(finish="hot_rolled", limit=100),
        SearchQuery(finish="hot_rolled", thickness_min_mm=2.0, thickness_max_mm=4.0, limit=100),
        SearchQuery(grade_ids=["S235JR"], include_unresolved=True, limit=100),
        SearchQuery(text="S355J2 5x2000", width_min_mm=1250, limit=100),
    ]
    for q in queries:
        a = {it.item_id for it in await pg.search(q)}
        b = {it.item_id for it in mem.search(q)}
        assert a == b, q
    target = items[0]
    q = SearchQuery(text=f"{target.grade_text} {target.thickness_mm:g}x{target.width_mm:g}", limit=5)
    assert target.item_id in [it.item_id for it in await pg.search(q)]
    assert target.item_id in [it.item_id for it in mem.search(q)]
    await eng.dispose()


async def _match_over_mcp(base: str, world):
    from steelmatch.agents import matcher as mt
    from steelmatch.domain.grades import GradeKB
    from steelmatch.domain.spec import LineSpec, Policy
    from steelmatch.settings import load
    from steelmatch.tools.mcp_client import McpTools

    from steelmatch.service.db import engine, sessions
    from steelmatch.service.search import PgInventory, PgTools

    eng = engine(URL)
    inv = PgInventory(sessions(eng))
    lots = await inv.search(__import__("steelmatch.search.inventory", fromlist=["SearchQuery"]).SearchQuery(limit=50))
    lot = lots[0]
    spec = LineSpec(line=1, form=lot.form if lot.form in ("coil", "strip") else "sheet", finish=lot.finish,
                    grade_id=lot.grade_id or "S235JR", thickness_mm=lot.thickness_mm, width_mm=lot.width_mm,
                    length_mm=lot.length_mm, quantity_t=min(5.0, lot.weight_t), coating=lot.coating, surface=lot.surface)
    direct = await mt.match_line(spec, Policy(upgrades_ok=True, secondary_ok=True), PgTools(inv, GradeKB.load(), load().fit,
                                 world.month.today), GradeKB.load(), load(), None)
    await eng.dispose()
    async with McpTools.connect(base + "/mcp/", "demo-readonly-token") as tools:
        over_mcp = await mt.match_line(spec, Policy(upgrades_ok=True, secondary_ok=True), tools, GradeKB.load(), load(), None)
    return direct, over_mcp


def test_agents_can_use_their_tools_over_mcp(server, world):
    # the contract test above left one supplier's list in the store; match against it through MCP
    direct, over_mcp = asyncio.run(_match_over_mcp(server, world))
    key = lambda o: [(f.item_id, f.status, f.landed_eur_t) for f in o.offers + o.review_offers]
    assert key(direct) and key(direct) == key(over_mcp)
    ov = httpx.get(server + "/api/overview", headers=TRADER).json()
    assert any(m["tool"] == "check_lots" or m["tool"] == "search_inventory" for m in ov["mcp"])
