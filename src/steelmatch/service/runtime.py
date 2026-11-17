"""The running service: the desk on Postgres, the inbound queue, the worker and the review loop."""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from ..agents import stocklist as sl
from ..desk import Desk, RfqResult
from ..docs.reader import Attachment, Inbound
from ..domain.grades import GradeKB
from ..llm.gateway import CallRecord, Gateway
from ..observe.langfuse_bridge import Observer
from ..settings import Settings, load
from .db import (CorrectionRow, EvalCaseRow, JobRow, JournalRow, LayoutRow, LearnedAliasRow, McpCallRow, ModelCallRow, RfqRow,
                 StockListRow, engine, sessions)
from .search import PgInventory, PgTools


def result_json(r: RfqResult, items: dict[str, Any]) -> dict:
    ex = r.extraction

    def offer(f) -> dict:
        it = items.get(f.item_id)
        return {"item_id": f.item_id, "status": f.status, "landed_eur_t": f.landed_eur_t, "usable_t": f.usable_t,
                "processing": f.processing, "grade_match": f.grade_match, "partial": f.partial, "lead_days": f.lead_days,
                "checks": [{"name": c.name, "status": c.status, "detail": c.detail} for c in f.checks],
                "item": it.model_dump(mode="json") if it else None}

    return {
        "status": r.status,
        "extraction": {
            "status": ex.status, "language": ex.language, "policy": ex.policy.model_dump(mode="json"),
            "review": [{"line": i.line, "field": i.field, "reason": i.reason, "proposals": list(i.proposals)} for i in ex.review],
            "evidence": ex.evidence, "invalid_quotes": ex.invalid_quotes,
        },
        "lines": [{"spec": l.spec.model_dump(mode="json"), "offers": [offer(f) for f in l.offers],
                   "review_offers": [offer(f) for f in l.review_offers], "needs_review": l.needs_review, "reasons": l.reasons,
                   "agent_found": l.agent_found, "stop_reason": l.stop_reason, "text": l.text, "steps": l.log} for l in r.lines],
        "cost_eur": r.cost_eur, "model_calls": r.model_calls, "prompt_versions": r.prompt_versions, "trace_id": r.trace_id,
    }


@dataclass
class PgJournal:
    sessions: async_sessionmaker
    replays: int = 0

    async def get(self, run_id: str, step: str) -> Any | None:
        async with self.sessions() as s:
            row = await s.get(JournalRow, (run_id, step))
            return None if row is None else row.value.get("v")

    async def put(self, run_id: str, step: str, value: Any) -> None:
        async with self.sessions() as s:
            await s.execute(insert(JournalRow).values(run_id=run_id, step=step, value={"v": value}).on_conflict_do_nothing())
            await s.commit()

    def steps(self, run_id: str) -> list:  # pragma: no cover - read through the API
        return []


@dataclass
class Runtime:
    engine: AsyncEngine
    sessions: async_sessionmaker
    settings: Settings
    gateway: Gateway
    desk: Desk
    inventory: PgInventory
    observer: Observer
    pending_calls: list[CallRecord] = field(default_factory=list)
    today_override: date | None = None

    @classmethod
    def create(cls, url: str | None = None, gateway: Gateway | None = None, observer: Observer | None = None) -> "Runtime":
        eng = engine(url)
        sess = sessions(eng)
        settings = load()
        gw = gateway or Gateway.from_file()
        inv = PgInventory(sess, settings.rrf_k)
        obs = observer or Observer.from_env()
        kb = GradeKB.load()
        desk = Desk(settings, gw, kb, inv, observer=obs, journal=PgJournal(sess),
                    tools_for=lambda d: PgTools(inv, desk.kb, settings.fit, d))
        rt = cls(eng, sess, settings, gw, desk, inv, obs)
        gw.listeners.append(rt.pending_calls.append)
        return rt

    def today(self) -> date:
        return self.today_override or date.today()

    async def startup(self) -> None:
        async with self.sessions() as s:
            for row in (await s.execute(select(LayoutRow))).scalars():
                self.desk.layouts[row.fingerprint] = sl.ColumnMap(**row.mapping)
            learned = {r.alias: r.grade_id for r in (await s.execute(select(LearnedAliasRow))).scalars()}
        self.desk.kb = GradeKB.load(learned=learned)

    async def flush_calls(self) -> None:
        calls, self.pending_calls[:] = list(self.pending_calls), []
        if not calls:
            return
        async with self.sessions() as s:
            s.add_all([ModelCallRow(route=c.route, model=c.model or "(failed)", agent=c.metadata.get("agent", ""), input_tokens=c.input_tokens,
                                    output_tokens=c.output_tokens, cost_eur=c.cost_eur, latency_ms=c.latency_ms, ok=c.ok,
                                    fallback=c.fallback) for c in calls])
            await s.commit()

    # Inbound -------------------------------------------------------------------------------------
    async def enqueue_inbound(self, msg: Inbound) -> bool:
        """Idempotent: the same message id is stored and queued once, however often Pub/Sub delivers it."""
        raw = msg.model_dump(mode="json", exclude={"attachments"}) | {
            "attachments": [{"filename": a.filename, "content_type": a.content_type, "data_b64": base64.b64encode(a.data).decode()}
                            for a in msg.attachments]}
        async with self.sessions() as s:
            res = await s.execute(insert(RfqRow).values(message_id=msg.message_id, received=msg.received, sender=msg.sender,
                                                        subject=msg.subject, status="queued", raw=raw).on_conflict_do_nothing())
            await s.execute(insert(JobRow).values(kind="rfq", ref=msg.message_id).on_conflict_do_nothing())
            await s.commit()
            return res.rowcount == 1

    async def import_list(self, supplier_id: str, country: str, filename: str, data: bytes) -> StockListRow:
        list_id = f"{supplier_id}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        async with self.sessions() as s:
            s.add(StockListRow(list_id=list_id, supplier_id=supplier_id, filename=filename, status="importing"))
            await s.commit()
        res = await self.desk.import_stocklist(supplier_id, country, filename, data, list_id, self.today())
        async with self.sessions() as s:
            row = await s.get(StockListRow, list_id)
            row.status, row.problems, row.items, row.skipped, row.used_model = res.status, res.problems, len(res.items), \
                len(res.skipped_rows), res.used_model
            if res.mapping is not None:
                from ..docs.reader import table_rows

                rows = table_rows(data, filename)
                fp = sl.fingerprint(rows[sl.header_row(rows)])
                await s.execute(insert(LayoutRow).values(fingerprint=fp, mapping=res.mapping.model_dump(), approved_by="validated")
                                .on_conflict_do_nothing())
            await s.commit()
        if res.status == "loaded":
            await self.inventory.retire_supplier(supplier_id, list_id)
        await self.flush_calls()
        async with self.sessions() as s:
            return await s.get(StockListRow, list_id)

    # Worker -----------------------------------------------------------------------------------------
    async def claim(self, lease_s: int = 120) -> JobRow | None:
        async with self.sessions() as s:
            row = (await s.execute(text("""
                UPDATE jobs SET status='running', attempts=attempts+1, lease_until=now() + make_interval(secs => :lease)
                WHERE id = (SELECT id FROM jobs WHERE status='queued' OR (status='running' AND lease_until < now())
                            ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1)
                RETURNING id, kind, ref, attempts"""), {"lease": lease_s})).first()
            await s.commit()
        if row is None:
            return None
        return JobRow(id=row.id, kind=row.kind, ref=row.ref, attempts=row.attempts)

    async def run_once(self, max_attempts: int = 3) -> str | None:
        job = await self.claim()
        if job is None:
            return None
        try:
            async with self.sessions() as s:
                rfq = await s.get(RfqRow, job.ref)
                raw = rfq.raw
            msg = Inbound(**{k: raw[k] for k in ("message_id", "sender", "subject", "body", "received")},
                          attachments=[Attachment(filename=a["filename"], content_type=a["content_type"], data=base64.b64decode(a["data_b64"]))
                                       for a in raw["attachments"]])
            result = await self.desk.process(msg, self.today())
            ids = [f.item_id for l in result.lines for f in l.offers + l.review_offers]
            items = {it.item_id: it for it in await self.inventory.items(ids)}
            async with self.sessions() as s:
                row = await s.get(RfqRow, job.ref)
                row.status, row.result, row.cost_eur, row.model_calls, row.trace_id = result.status, result_json(result, items), \
                    result.cost_eur, result.model_calls, result.trace_id
                await s.execute(text("UPDATE jobs SET status='done', lease_until=NULL WHERE id=:id"), {"id": job.id})
                await s.commit()
            await self.flush_calls()
            return job.ref
        except Exception as e:  # the job is retried until it runs out of attempts
            status = "failed" if job.attempts >= max_attempts else "queued"
            async with self.sessions() as s:
                await s.execute(text("UPDATE jobs SET status=:st, last_error=:err, lease_until=NULL WHERE id=:id"),
                                {"st": status, "err": f"{type(e).__name__}: {e}", "id": job.id})
                if status == "failed":
                    await s.execute(text("UPDATE rfqs SET status='failed' WHERE message_id=:m"), {"m": job.ref})
                await s.commit()
            return None

    async def drain(self, limit: int = 1000) -> int:
        n = 0
        while n < limit and await self.run_once():
            n += 1
        return n

    # Review -----------------------------------------------------------------------------------------
    async def correct(self, message_id: str, line: int, field: str, value: str, by: str) -> dict:
        """A person's correction: stored, turned into an eval case, and scored in Langfuse as a CORRECTION."""
        async with self.sessions() as s:
            rfq = await s.get(RfqRow, message_id)
            if rfq is None or rfq.result is None:
                raise KeyError(message_id)
            before = None
            for l in rfq.result["lines"]:
                if l["spec"]["line"] == line:
                    before = l["spec"].get(field)
            if field == "grade":
                before = next((e["quote"] for k, e in rfq.result["extraction"]["evidence"].items() if k == f"{line}.grade"), before)
            s.add(CorrectionRow(message_id=message_id, line=line, field=field, before=None if before is None else str(before),
                                after=value, by=by))
            case_id = f"{message_id}"
            expected = (await s.get(EvalCaseRow, case_id))
            exp = expected.expected if expected else {"lines": [l["spec"] | {"line": l["spec"]["line"]} for l in rfq.result["lines"]]}
            target = next((l for l in exp["lines"] if l["line"] == line), None)
            if target is None:
                target = {"line": line}
                exp["lines"].append(target)
            target["grade_id" if field == "grade" else field] = _typed(field, value)
            await s.merge(EvalCaseRow(case_id=case_id, message_id=message_id, expected=exp))
            await s.commit()
            self.observer.correction(rfq.trace_id, field, before, value, comment=f"by {by}")
            self.observer.add_case("steel-rfq-corrections", case_id, {"message_id": message_id}, exp, trace_id=rfq.trace_id)
        return {"case_id": case_id, "expected": exp}

    async def approve(self, message_id: str, by: str) -> None:
        async with self.sessions() as s:
            rfq = await s.get(RfqRow, message_id)
            if rfq is None:
                raise KeyError(message_id)
            rfq.status = "sent"
            await s.commit()

    async def log_mcp(self, tool: str, client_id: str, outcome: str, ms: float, detail: str | None = None) -> None:
        async with self.sessions() as s:
            s.add(McpCallRow(tool=tool, client_id=client_id, outcome=outcome, latency_ms=ms, detail=detail))
            await s.commit()

    # Read models for the console --------------------------------------------------------------------
    async def overview(self) -> dict:
        async with self.sessions() as s:
            by_status = dict((await s.execute(select(RfqRow.status, func.count()).group_by(RfqRow.status))).all())
            cost = (await s.execute(select(func.coalesce(func.sum(ModelCallRow.cost_eur), 0.0)))).scalar()
            calls = (await s.execute(select(ModelCallRow.model, func.count(), func.sum(ModelCallRow.cost_eur),
                                            func.percentile_cont(0.5).within_group(ModelCallRow.latency_ms),
                                            func.sum(func.cast(ModelCallRow.fallback, type_=__import__("sqlalchemy").Integer)))
                                     .group_by(ModelCallRow.model))).all()
            lists = dict((await s.execute(select(StockListRow.status, func.count()).group_by(StockListRow.status))).all())
            items = (await s.execute(text("SELECT count(*) FROM stock_items WHERE active"))).scalar()
            mcp = (await s.execute(select(McpCallRow.tool, McpCallRow.outcome, func.count()).group_by(McpCallRow.tool, McpCallRow.outcome))).all()
            cases = (await s.execute(select(func.count()).select_from(EvalCaseRow))).scalar()
            aliases = (await s.execute(select(func.count()).select_from(LearnedAliasRow))).scalar()
        return {"rfqs": by_status, "model_cost_eur": round(cost, 4), "lists": lists, "active_lots": items, "eval_cases": cases,
                "learned_aliases": aliases,
                "models": [{"model": m, "calls": n, "cost_eur": round(c or 0, 5), "p50_ms": round(p or 0), "fallbacks": f or 0}
                           for m, n, c, p, f in calls],
                "mcp": [{"tool": t, "outcome": o, "calls": n} for t, o, n in mcp]}

    async def list_rfqs(self, status: str | None, limit: int = 100) -> list[dict]:
        async with self.sessions() as s:
            q = select(RfqRow).order_by(RfqRow.received.desc(), RfqRow.message_id).limit(limit)
            if status:
                q = q.where(RfqRow.status == status)
            rows = (await s.execute(q)).scalars().all()
        return [{"message_id": r.message_id, "received": r.received, "sender": r.sender, "subject": r.subject, "status": r.status,
                 "lines": len((r.result or {}).get("lines", [])),
                 "offers": sum(len(l["offers"]) for l in (r.result or {}).get("lines", [])),
                 "review_items": len((r.result or {}).get("extraction", {}).get("review", [])), "cost_eur": r.cost_eur} for r in rows]

    async def get_rfq(self, message_id: str) -> dict | None:
        async with self.sessions() as s:
            r = await s.get(RfqRow, message_id)
            if r is None:
                return None
            return {"message_id": r.message_id, "received": r.received, "sender": r.sender, "subject": r.subject, "status": r.status,
                    "body": r.raw["body"], "attachments": [a["filename"] for a in r.raw["attachments"]], "result": r.result,
                    "cost_eur": r.cost_eur, "model_calls": r.model_calls}


def _typed(field: str, value: str):
    if field.endswith("_mm") or field.endswith("_t") or field.endswith("_eur_t"):
        return float(value)
    return value
