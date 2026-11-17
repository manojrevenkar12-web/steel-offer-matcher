"""Builds a simulated month and runs one design through it.

Designs:
- manual: a trader reads every list and email and searches by hand (perfect reading, literal
  search, limited hours);
- model_decides: a model reads lists and emails and picks offers itself, and they go out;
- platform: this repository (the model reads and quotes, code decides, people review);
- platform_no_agent: the platform with the matching agent switched off (ablation).
Every design sees the same month, the same documents and the same model mistakes.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import litellm

from steelmatch.agents import matcher as mt
from steelmatch.agents import stocklist as sl
from steelmatch.desk import Desk, RfqResult
from steelmatch.docs.reader import Attachment, Inbound
from steelmatch.domain.fit import check, rank
from steelmatch.domain.grades import GradeKB
from steelmatch.domain.spec import LineSpec, Policy, StockItem
from steelmatch.llm.bridge import gateway_model
from steelmatch.llm.gateway import Gateway
from steelmatch.review.learning import AliasLearner
from steelmatch.search.inventory import MemoryInventory, SearchQuery
from steelmatch.settings import Settings
from steelmatch.settings import load as load_settings
from steelmatch.tools.base import LocalTools
from steelworld import market, truth
from steelworld.catalog import BY_ID
from steelworld.documents import RfqDoc, StockList, render_rfq, stock_list
from steelworld.simllm import Registry, SimLLM, body_key

ROOT = Path(__file__).resolve().parents[2]
LIST_DAYS = (0, 5, 10, 15)


@dataclass
class World:
    month: market.Month
    cfg: dict
    docs: dict[str, RfqDoc]
    lists: dict[int, list[StockList]]  # day -> lists sent that day
    registry: Registry


def build(seed: int, heldout: bool = False) -> World:
    m = market.generate(seed, heldout)
    cfg = market.settings()
    reg = Registry()
    docs = {}
    for rfq in m.rfqs:
        d = render_rfq(m, rfq)
        docs[rfq.message_id] = d
        reg.rfqs[body_key(d.message["body"])] = d.claims
        reg.images.update(d.images)
    lists: dict[int, list[StockList]] = {}
    for v, day in enumerate(LIST_DAYS):
        lists[day] = []
        for s in m.suppliers:
            lots = [l for l in s.lots if m.sold.get(l.lot_id, 10**9) > day]
            sl_ = stock_list(s, lots, v)
            lists[day].append(sl_)
            reg.layouts[tuple(sl_.headers)] = sl_.mapping
    return World(m, cfg, docs, lists, reg)


def install_sim(world: World, label: str) -> SimLLM:
    sim = SimLLM(world.month.seed, world.registry, world.cfg["models"], label)
    litellm.custom_provider_map = [{"provider": "sim", "custom_handler": sim}]
    from litellm.utils import custom_llm_setup

    custom_llm_setup()
    return sim


def inbound(msg: dict) -> Inbound:
    return Inbound(message_id=msg["message_id"], sender=msg["sender"], subject=msg["subject"], body=msg["body"],
                   received=msg["received"], attachments=[Attachment(**a) for a in msg["attachments"]])


def true_spec(rfq: market.Rfq, line: market.Line, i: int) -> LineSpec:
    m = BY_ID[line.material]
    alts = []
    if line.alt_thickness_mm:
        alts.append({"thickness_mm": line.alt_thickness_mm})
    if line.width_min_mm:
        alts.append({"width_min_mm": line.width_min_mm})
    return LineSpec(line=i, form=line.form, finish=m.finish, grade_id=line.material, grade_text=line.designation,
                    coating=line.coating, surface=line.surface, thickness_mm=line.thickness_mm, width_mm=line.width_mm,
                    length_mm=line.length_mm, quantity_t=line.quantity_t, max_price_eur_t=line.max_price_eur_t,
                    delivery_country=rfq.buyer.country, delivery_by=rfq.delivery_by, alternatives=alts)


def true_policy(rfq: market.Rfq) -> Policy:
    p = rfq.policy
    return Policy(upgrades_ok=p.upgrades_ok, secondary_ok=p.secondary_ok, certificate=p.cert, excluded_origins=list(p.excluded))


@dataclass
class Sent:
    message_id: str
    line_offers: dict[int, list[tuple[str, float]]]  # true line index -> [(lot id, landed claimed)]
    minutes: float
    review: bool = False
    review_items: int = 0
    lost: bool = False
    status: str = ""
    agent_found_sent: int = 0
    cost_eur: float = 0.0
    latency_ms: float = 0.0
    calls: int = 0
    reasons: list[str] = field(default_factory=list)


@dataclass
class RunLog:
    design: str
    sent: list[Sent] = field(default_factory=list)
    list_minutes: float = 0.0
    lists_held: int = 0
    lists_loaded: int = 0
    mapping_model_calls: int = 0
    cost_eur: float = 0.0
    gateway: dict = field(default_factory=dict)
    aliases_learned: list[str] = field(default_factory=list)
    alias_rejected: list[str] = field(default_factory=list)
    unresolved_by_week: list[int] = field(default_factory=list)
    stop_reasons: dict[str, int] = field(default_factory=dict)
    journal_replays: int = 0


def _lot_map(lists: list[StockList]) -> dict[str, str]:
    out = {}
    for s in lists:
        list_id = s.filename.rsplit(".", 1)[0]
        for i, lot_id in enumerate(s.row_lots, start=1):
            out[f"{list_id}-{i:04d}"] = lot_id
    return out


# ------------------------------------------------------------------------------------------
# Platform


async def run_platform(world: World, use_agent: bool = True, settings: Settings | None = None, label: str = "platform") -> RunLog:
    install_sim(world, label)
    settings = settings or load_settings()
    gateway = Gateway.from_file()
    kb = GradeKB.load()
    desk = Desk(settings, gateway, kb, MemoryInventory(rrf_k=settings.rrf_k), use_matching_agent=use_agent)
    learner = AliasLearner(settings.alias_min_corrections)
    log = RunLog(label)
    m = world.month
    cfg = world.cfg
    mins = cfg["minutes"]
    lot_of: dict[str, str] = {}
    supplier_items: dict[str, list[str]] = {}
    unresolved_week = [0, 0, 0, 0]
    rfqs_by_day: dict[int, list[market.Rfq]] = {}
    for r in m.rfqs:
        rfqs_by_day.setdefault(r.day, []).append(r)
    for day in range(m.days):
        today = m.today + timedelta(days=day)
        if day in world.lists:
            for s in world.lists[day]:
                desk.inventory.remove(supplier_items.get(s.supplier_id, []))
                list_id = s.filename.rsplit(".", 1)[0]
                res = await desk.import_stocklist(s.supplier_id, s.country, s.filename, s.data, list_id, today)
                log.mapping_model_calls += 1 if res.used_model else 0
                if res.status == "held":
                    # a trader maps the layout by hand once; the platform remembers it
                    log.lists_held += 1
                    log.list_minutes += mins["stocklist_manual_per_list"]
                    desk.layouts[sl.fingerprint(s.headers)] = sl.ColumnMap(**s.mapping)
                    res = await desk.import_stocklist(s.supplier_id, s.country, s.filename, s.data, list_id, today)
                log.lists_loaded += 1
                supplier_items[s.supplier_id] = [it.item_id for it in res.items]
            lot_of.update(_lot_map(world.lists[day]))
        for rfq in rfqs_by_day.get(day, []):
            doc = world.docs[rfq.message_id]
            before = len(gateway.records)
            result = await desk.process(inbound(doc.message), today)
            calls = gateway.records[before:]
            sent = await _platform_send(world, desk, rfq, result, lot_of, learner, today, settings)
            sent.cost_eur = sum(c.cost_eur for c in calls)
            sent.latency_ms = sum(c.latency_ms for c in calls)
            sent.calls = len(calls)
            log.sent.append(sent)
            for l in result.lines:
                log.stop_reasons[l.stop_reason] = log.stop_reasons.get(l.stop_reason, 0) + 1
            unresolved_week[min(3, day // 5)] += len(result.extraction.unresolved_grades)
        if day % 5 == 4:  # weekly: learn aliases from corrections, after replay
            before_aliases = set(desk.kb.learned)
            desk.kb = learner.learn(desk.kb)
            log.aliases_learned += sorted(set(desk.kb.learned) - before_aliases)
    log.alias_rejected = [d.alias for d in learner.decisions if not d.accepted]
    log.unresolved_by_week = unresolved_week
    log.cost_eur = sum(r.cost_eur for r in gateway.records)
    log.gateway = gateway.summary()
    log.journal_replays = getattr(desk.journal, "replays", 0)
    return log


async def _platform_send(world: World, desk: Desk, rfq: market.Rfq, result: RfqResult, lot_of: dict[str, str],
                         learner: AliasLearner, today: date, settings: Settings) -> Sent:
    mins = world.cfg["minutes"]
    sent = Sent(rfq.message_id, {}, 0.0, status=result.status)
    if result.status == "not_rfq":
        return sent
    if not rfq.is_rfq:  # the platform thought it was an RFQ: a trader dismisses it
        sent.minutes += mins["review_item"]
        sent.review = True
        return sent
    tools = LocalTools(desk.inventory, desk.kb, settings.fit, today)
    review_items = list(result.extraction.review)
    # policy-level review items (line 0) mean a person rereads the email's conditions
    policy = true_policy(rfq) if any(it.line == 0 for it in review_items) else result.extraction.policy
    by_line = {l.spec.line: l for l in result.lines}
    for i, tline in enumerate(rfq.lines, start=1):
        lr = by_line.get(i)
        flagged = any(it.line == i for it in review_items)
        if lr is None or flagged:
            # a person fixes the flagged line from the email, and the designation is remembered
            for it in review_items:
                if it.line == i and it.field == "grade":
                    learner.record(rfq.message_id, tline.designation, tline.material)
            out = await mt.match_line(true_spec(rfq, tline, i), policy, tools, desk.kb, settings, None)
            picked, review_offers, agent_found = out.offers, out.review_offers, []
        else:
            if lr.spec.grade_id == tline.material:
                learner.confirm(rfq.message_id, tline.designation, tline.material)
            picked, review_offers, agent_found = lr.offers, lr.review_offers, lr.agent_found
        if not picked and review_offers:
            sent.minutes += mins["review_item"]  # the trader asks the buyer about the open point
            picked = [f for f in review_offers if _truly_ok(world, rfq, tline, lot_of.get(f.item_id), today)]
        sent.line_offers[i] = [(lot_of[f.item_id], f.landed_eur_t) for f in picked if f.item_id in lot_of]
        sent.agent_found_sent += sum(1 for f in picked if f.item_id in agent_found)
        sent.minutes += mins["approve_offer"] * max(1, len(picked))
        sent.reasons.extend(f"{it.field}" for it in review_items if it.line == i)
        if lr is not None:
            sent.reasons.extend(r.split(":")[0] for r in lr.reasons)
    sent.reasons.extend(f"{it.field}" for it in review_items if it.line == 0)
    sent.review_items = len(review_items) + sum(1 for l in result.lines if l.needs_review)
    sent.review = result.status == "review"
    sent.minutes += mins["review_item"] * len(review_items)
    return sent


def _truly_ok(world: World, rfq: market.Rfq, line: market.Line, lot_id: str | None, today: date) -> bool:
    if lot_id is None:
        return False
    lot = world.month.lots()[lot_id]
    return truth.fit(line, rfq.policy, lot, rfq.buyer.country, rfq.delivery_by, today, world.cfg).ok


# ------------------------------------------------------------------------------------------
# Model decides

PICKER = "# picker\nYou pick supplier lots for a request and quote a delivered price per tonne. Reply with the lots to offer."


async def run_model_decides(world: World) -> RunLog:
    from pydantic import BaseModel
    from pydantic_ai import Agent

    from steelmatch.agents import extractor as ex
    from steelmatch.docs.reader import read
    from steelmatch.observe.langfuse_bridge import local_prompt

    class Pick(BaseModel):
        item_id: str
        price_eur_t: float

    class Picks(BaseModel):
        picks: list[Pick] = []

    install_sim(world, "model_decides")
    settings = load_settings()
    gateway = Gateway.from_file()
    kb = GradeKB.load()
    inv = MemoryInventory(rrf_k=settings.rrf_k)
    log = RunLog("model_decides")
    m = world.month
    mins = world.cfg["minutes"]
    lot_of: dict[str, str] = {}
    supplier_items: dict[str, list[str]] = {}
    mapper = sl.build_agent(gateway_model(gateway, settings.routes["stocklist_mapping"], "stocklist_mapper"), local_prompt("stocklist_mapper").text)
    extractor = ex.build_agent(gateway_model(gateway, settings.routes["extract"], "extractor"), local_prompt("extractor").text)
    picker = Agent(gateway_model(gateway, settings.routes["match"], "picker"), output_type=Picks, instructions=PICKER, name="picker")
    rfqs_by_day: dict[int, list[market.Rfq]] = {}
    for r in m.rfqs:
        rfqs_by_day.setdefault(r.day, []).append(r)
    for day in range(m.days):
        today = m.today + timedelta(days=day)
        if day in world.lists:
            from steelmatch.docs.reader import table_rows

            for s in world.lists[day]:
                inv.remove(supplier_items.get(s.supplier_id, []))
                rows = table_rows(s.data, s.filename)
                h = sl.header_row(rows)
                try:
                    res = await mapper.run(sl.mapping_prompt(rows, h))
                    mapping = res.output
                except Exception:
                    continue
                items, _ = sl.read_rows(mapping, rows[h], rows[h + 1:], s.supplier_id, s.country, kb, today, s.filename.rsplit(".", 1)[0])
                inv.add(items)
                supplier_items[s.supplier_id] = [it.item_id for it in items]
                log.lists_loaded += 1
            lot_of.update(_lot_map(world.lists[day]))
        for rfq in rfqs_by_day.get(day, []):
            doc = world.docs[rfq.message_id]
            before = len(gateway.records)
            sent = Sent(rfq.message_id, {}, mins["model_decides_check"])
            try:
                claims = (await extractor.run(read(inbound(doc.message)).render(), deps=ex.ExtractDeps(kb))).output
            except Exception:
                claims = None
            if claims and claims.is_rfq:
                for i, lc in enumerate(claims.lines, start=1):
                    if lc.thickness_mm is None or lc.width_mm is None:
                        continue
                    q = SearchQuery(text=f"{lc.grade or ''} {lc.thickness_mm:g}x{lc.width_mm:g}", limit=30)
                    lots = [it.model_dump(mode="json", exclude={"grade_id", "supplier_id"}) | {"grade": it.grade_text} for it in inv.search(q)]
                    payload = {"requested": lc.model_dump(mode="json") | {"grade": lc.grade},
                               "buyer_allows": claims.policy.model_dump(mode="json"), "lots": lots}
                    try:
                        res = await picker.run("Pick lots.\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```")
                        sent.line_offers[i] = [(lot_of[p.item_id], p.price_eur_t) for p in res.output.picks if p.item_id in lot_of]
                    except Exception:
                        pass
                    sent.minutes += mins["approve_offer"] * 0.5
            calls = gateway.records[before:]
            sent.cost_eur = sum(c.cost_eur for c in calls)
            sent.latency_ms = sum(c.latency_ms for c in calls)
            sent.calls = len(calls)
            log.sent.append(sent)
    log.cost_eur = sum(r.cost_eur for r in gateway.records)
    log.gateway = gateway.summary()
    return log


# ------------------------------------------------------------------------------------------
# Manual


def run_manual(world: World) -> RunLog:
    from steelworld import rand

    m = world.month
    mins = world.cfg["minutes"]
    log = RunLog("manual")
    lots = m.lots()
    on_list: dict[str, list[str]] = {}
    queue_minutes = 0.0
    for day in range(m.days):
        today = m.today + timedelta(days=day)
        if day in world.lists:
            for s in world.lists[day]:
                on_list[s.supplier_id] = list(s.row_lots)
                log.list_minutes += mins["stocklist_manual_per_list"]
                log.lists_loaded += 1
        stock = [lots[i] for ids in on_list.values() for i in ids]
        for rfq in [r for r in m.rfqs if r.day == day]:
            r = rand.of(m.seed, "manual", rfq.message_id)
            if not rfq.is_rfq:
                log.sent.append(Sent(rfq.message_id, {}, 1.0))
                continue
            minutes = mins["manual_read_rfq"] + mins["manual_quote_write"]
            offers: dict[int, list[tuple[str, float]]] = {}
            for i, line in enumerate(rfq.lines, start=1):
                minutes += mins["manual_per_line"]
                use_alt = r.random() < 0.5
                consider_slit = r.random() < 0.3
                consider_upgrade = rfq.policy.upgrades_ok and r.random() < 0.5
                found = []
                for lot in stock:
                    if lot.material != line.material and not (consider_upgrade and lot.material in _higher(line.material)):
                        continue
                    ts = [line.thickness_mm] + ([line.alt_thickness_mm] if use_alt and line.alt_thickness_mm else [])
                    if not any(abs(lot.thickness_mm - t) <= 0.05 for t in ts):
                        continue
                    wmin = line.width_min_mm if use_alt and line.width_min_mm else line.width_mm
                    if not (wmin <= lot.width_mm <= line.width_mm + 20 or (consider_slit and lot.width_mm >= 2 * line.width_mm)):
                        continue
                    if line.form in ("coil", "strip") and lot.form in ("sheet", "plate"):
                        continue
                    if line.form in ("sheet", "plate") and lot.form in ("sheet", "plate") and line.length_mm and lot.length_mm != line.length_mm:
                        continue
                    if lot.quality == "secondary" and not rfq.policy.secondary_ok and r.random() > 0.05:
                        continue
                    if truth.CERT[lot.cert] < truth.CERT[rfq.policy.cert] and r.random() > 0.08:
                        continue
                    if (lot.origin in ("RU", "BY") or lot.origin in rfq.policy.excluded) and r.random() > 0.1:
                        continue
                    if lot.weight_t < line.quantity_t * 0.5:
                        continue
                    if (line.coating or lot.coating) and line.coating != lot.coating and r.random() > 0.05:
                        continue
                    if line.surface and lot.surface != line.surface and r.random() > 0.1:
                        continue
                    if line.max_price_eur_t and lot.price_eur_t + 40 > line.max_price_eur_t:
                        continue
                    found.append(lot)
                found.sort(key=lambda l: l.price_eur_t)
                offers[i] = [(l.lot_id, l.price_eur_t + 40) for l in found[:2]]
            log.sent.append(Sent(rfq.message_id, offers, minutes))
    return log


def _higher(material: str) -> list[str]:
    from steelworld.catalog import MATERIALS, can_replace

    return [x.id for x in MATERIALS if can_replace(material, x.id)]
