"""The desk: one place where an inbound email becomes extracted lines and ranked offers.

Steps of an RFQ run are journaled (transcription, extraction, matching per line), so a run that
crashes resumes without paying for the same model calls twice. Every run is traced in Langfuse
when it is configured, with the prompt version each agent used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import inspect
from typing import Any, Callable

from pydantic_ai.messages import BinaryContent

from .agents import extractor as ex
from .agents import matcher as mt
from .agents import stocklist as sl
from .docs.reader import DocumentText, Inbound, Segment, read
from .domain.fit import Fit
from .domain.grades import GradeKB
from .domain.spec import LineSpec, Policy, StockItem
from .harness.journal import JournalStore, MemoryJournal, step
from .llm.bridge import gateway_model
from .llm.gateway import Gateway, GatewayError
from .observe.langfuse_bridge import Observer
from .review.explain import offer_text
from .search.inventory import MemoryInventory
from .settings import Settings
from .tools.base import LocalTools, MatchTools


@dataclass
class LineResult:
    spec: LineSpec
    offers: list[Fit]
    review_offers: list[Fit]
    needs_review: bool
    reasons: list[str]
    agent_found: list[str]
    stop_reason: str
    text: str
    log: list[dict] = field(default_factory=list)


@dataclass
class RfqResult:
    message_id: str
    status: str  # "not_rfq" | "ready" | "review"
    extraction: ex.Extraction
    lines: list[LineResult]
    cost_eur: float
    model_calls: int
    prompt_versions: dict[str, str]
    trace_id: str | None = None

    @property
    def needs_review(self) -> bool:
        return self.status == "review"


@dataclass
class Desk:
    settings: Settings
    gateway: Gateway
    kb: GradeKB
    inventory: MemoryInventory
    observer: Observer = field(default_factory=lambda: Observer(None))
    journal: JournalStore = field(default_factory=MemoryJournal)
    tools_for: Callable[[date], MatchTools] | None = None  # e.g. the Postgres-backed tools in the service
    use_matching_agent: bool = True
    layouts: dict[str, sl.ColumnMap] = field(default_factory=dict)  # layout fingerprint -> approved mapping

    def __post_init__(self) -> None:
        self.gateway.listeners.append(self._on_call)
        self._calls: list[Any] = []

    def _on_call(self, rec: Any) -> None:
        self._calls.append(rec)
        self.observer.generation(f"llm.{rec.metadata.get('agent', rec.route)}", rec.model, rec.input_tokens, rec.output_tokens,
                                 rec.cost_eur, {"route": rec.route, "fallback": rec.fallback}, rec.ok)

    def _agent_model(self, route_key: str, agent: str):
        return gateway_model(self.gateway, self.settings.routes[route_key], agent)

    # Stock lists ----------------------------------------------------------------------------
    async def import_stocklist(self, supplier_id: str, supplier_country: str, filename: str, data: bytes, list_id: str,
                               today: date) -> sl.ImportResult:
        from .docs.reader import table_rows

        rows = table_rows(data, filename)
        h = sl.header_row(rows)
        headers, body = rows[h], rows[h + 1:]
        fp = sl.fingerprint(headers)
        mapping = self.layouts.get(fp)
        used_model = False
        problems: list[str] = []
        if mapping is None:
            used_model = True
            prompt = self.observer.prompt("stocklist_mapper")
            for route in ("stocklist_mapping", "extract_hard"):
                agent = sl.build_agent(self._agent_model(route, "stocklist_mapper"), prompt.text)
                try:
                    res = await agent.run(sl.mapping_prompt(rows, h))
                except GatewayError as e:
                    problems = [f"model unavailable: {e}"]
                    continue
                problems = sl.validate(res.output, headers, body)
                if not problems:
                    mapping = res.output
                    self.layouts[fp] = mapping
                    break
        if mapping is None:
            return sl.ImportResult("held", [], None, problems, used_model=used_model)
        items, skipped = sl.read_rows(mapping, headers, body, supplier_id, supplier_country, self.kb, today, list_id)
        added = self.inventory.add(items)
        if inspect.isawaitable(added):
            await added
        return sl.ImportResult("loaded", items, mapping, [], skipped, used_model)

    # RFQs -------------------------------------------------------------------------------------
    async def process(self, msg: Inbound, today: date) -> RfqResult:
        start_calls = len(self._calls)
        run_id = msg.message_id
        prompts = {n: self.observer.prompt(n) for n in ("extractor", "matcher", "transcriber")}
        tools = self.tools_for(today) if self.tools_for else LocalTools(self.inventory, self.kb, self.settings.fit, today)
        with self.observer.span("rfq", as_type="agent", input={"subject": msg.subject, "sender": msg.sender},
                                metadata={"message_id": run_id, **{f"prompt.{k}": v.version for k, v in prompts.items()}}) as span:
            doc = read(msg)
            transcribed: set[str] = set()
            for img in doc.images:
                text = await step(self.journal, run_id, f"transcribe:{img.filename}",
                                  lambda img=img: self._transcribe(img, prompts["transcriber"].text))
                if text:
                    src = f"{img.filename} (transcribed)"
                    doc.segments.append(Segment(src, text))
                    transcribed.add(src)

            claims_raw = await step(self.journal, run_id, "extract", lambda: self._extract(doc, prompts["extractor"].text))
            if claims_raw is None:
                extraction = ex.Extraction("review", [], Policy(), [ex.ReviewItem(0, "model", "extraction model unavailable")])
            else:
                extraction = ex.decide(ex.RfqClaims.model_validate(claims_raw), doc, self.kb, self.settings.required_fields,
                                       today, transcribed)
                limit = self.settings.escalate_on_invalid_fields
                numeric_gap = any(it.field in ("thickness_mm", "width_mm", "length_mm", "quantity_t", "grade", "form")
                                  and not it.proposals for it in extraction.review)
                if extraction.invalid_quotes >= limit or numeric_gap:
                    hard_raw = await step(self.journal, run_id, "extract:strong",
                                          lambda: self._extract(doc, prompts["extractor"].text, hard=True))
                    if hard_raw is not None:
                        hard = ex.decide(ex.RfqClaims.model_validate(hard_raw), doc, self.kb, self.settings.required_fields,
                                         today, transcribed)
                        if (len(hard.review), hard.invalid_quotes) < (len(extraction.review), extraction.invalid_quotes):
                            extraction = hard

            lines: list[LineResult] = []
            if extraction.status != "not_rfq":
                agent = None
                if self.use_matching_agent:
                    agent = mt.build_agent(self._agent_model("match", "matcher"), prompts["matcher"].text)
                for spec in extraction.lines:
                    out = await mt.match_line(spec, extraction.policy, tools, self.kb, self.settings, agent)
                    items = {it.item_id: it for it in await tools.items([f.item_id for f in out.offers + out.review_offers])}
                    text = offer_text(spec, [(f, items[f.item_id]) for f in out.offers if f.item_id in items],
                                      extraction.language if extraction.language in ("de", "en") else self.settings.explain_language)
                    lines.append(LineResult(spec, out.offers, out.review_offers, out.needs_review, out.reasons, out.agent_found,
                                            out.stop_reason, text, out.log))
            status = extraction.status
            if status == "ready" and any(l.needs_review for l in lines):
                status = "review"
            calls = self._calls[start_calls:]
            cost = round(sum(c.cost_eur for c in calls), 6)
            span.update(output={"status": status, "lines": len(lines), "offers": sum(len(l.offers) for l in lines), "cost_eur": cost})
            trace_id = getattr(span, "trace_id", None)
        return RfqResult(run_id, status, extraction, lines, cost, len(calls), {k: v.version for k, v in prompts.items()}, trace_id)

    async def _extract(self, doc: DocumentText, instructions: str, hard: bool = False) -> dict | None:
        agent = ex.build_agent(self._agent_model("extract_hard" if hard else "extract", "extractor"), instructions)
        try:
            res = await agent.run(doc.render(), deps=ex.ExtractDeps(self.kb),
                                  usage_limits=ex.limits(self.settings.harness.max_model_requests,
                                                         self.settings.harness.max_input_tokens))
        except GatewayError:
            return None
        except Exception:
            return None
        return res.output.model_dump(mode="json")

    async def _transcribe(self, img, instructions: str) -> str | None:
        from pydantic_ai import Agent

        agent = Agent(self._agent_model("vision", "transcriber"), output_type=str, instructions=instructions, name="transcriber")
        try:
            res = await agent.run([f"Transcribe {img.filename}.", BinaryContent(data=img.data, media_type=img.content_type)])
        except Exception:
            return None
        return res.output
