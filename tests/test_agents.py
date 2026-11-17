"""Agents on Pydantic AI, models behind the LiteLLM gateway, simulated offline."""

import copy
from datetime import timedelta

import litellm
import pytest

from steeleval.harness import build, inbound
from steelmatch.agents import matcher as mt
from steelmatch.desk import Desk
from steelmatch.domain.spec import LineSpec, Policy, StockItem
from steelmatch.harness.journal import MemoryJournal
from steelmatch.llm.bridge import gateway_model
from steelmatch.llm.gateway import Gateway
from steelmatch.search.inventory import MemoryInventory
from steelmatch.settings import Settings
from steelmatch.tools.base import LocalTools
from steelworld.simllm import SimLLM

from .conftest import TODAY


@pytest.fixture(scope="module")
def world():
    w = build(11)
    w.month.rfqs = [r for r in w.month.rfqs if r.day == 0]
    return w


def install(world, **overrides):
    profiles = copy.deepcopy(world.cfg["models"])
    for model, changes in overrides.items():
        profiles[model].update(changes)
    sim = SimLLM(world.month.seed, world.registry, profiles, "test")
    litellm.custom_provider_map = [{"provider": "sim", "custom_handler": sim}]
    from litellm.utils import custom_llm_setup

    custom_llm_setup()
    return sim


def desk_for(world, settings, kb, **kw):
    d = Desk(settings, Gateway.from_file(), kb, MemoryInventory(), **kw)
    return d


async def test_rfq_runs_end_to_end_and_books_cost(world, settings, kb):
    install(world)
    desk = desk_for(world, settings, kb)
    for s in world.lists[0][:6]:
        res = await desk.import_stocklist(s.supplier_id, s.country, s.filename, s.data, s.filename.split(".")[0], TODAY)
        assert res.status in ("loaded", "held")
    rfq = next(r for r in world.month.rfqs if r.is_rfq and r.fmt == "prose")
    result = await desk.process(inbound(world.docs[rfq.message_id].message), world.month.today)
    assert result.status in ("ready", "review")
    assert result.model_calls >= 1 and result.cost_eur > 0
    assert set(result.prompt_versions) == {"extractor", "matcher", "transcriber"}


async def test_failing_route_falls_back_to_the_strong_model(world, settings, kb):
    install(world, **{"sim/fast-commercial": {"transient_error": 1.0}})
    gw = Gateway.from_file()
    from steelmatch.agents import extractor as ex
    from steelmatch.docs.reader import read
    from steelmatch.observe.langfuse_bridge import local_prompt

    agent = ex.build_agent(gateway_model(gw, "extract", "extractor"), local_prompt("extractor").text)
    rfq = next(r for r in world.month.rfqs if r.is_rfq)
    out = await agent.run(read(inbound(world.docs[rfq.message_id].message)).render(), deps=ex.ExtractDeps(kb))
    assert out.output.is_rfq
    assert gw.records and all(r.model == "sim/strong-commercial" and r.fallback for r in gw.records)


async def test_journal_resumes_without_paying_twice(world, settings, kb):
    install(world)
    journal = MemoryJournal()
    desk = desk_for(world, settings, kb, journal=journal, use_matching_agent=False)
    rfq = next(r for r in world.month.rfqs if r.is_rfq)
    msg = inbound(world.docs[rfq.message_id].message)
    first = await desk.process(msg, world.month.today)
    second = await desk.process(msg, world.month.today)
    assert first.model_calls >= 1 and second.model_calls == 0
    assert journal.replays >= 1


def lot(item_id, **kw):
    base = dict(item_id=item_id, supplier_id="S", form="coil", finish="hot_rolled", grade_id="S235JR", grade_text="S235JR",
                thickness_mm=3.0, width_mm=1500, weight_t=20, price_eur_t=650, country="DE", origin="DE", certificate="3.1")
    return StockItem(**(base | kw))


SPEC = LineSpec(line=1, form="coil", finish="hot_rolled", grade_id="S235JR", grade_text="St 37-2", thickness_mm=2.5,
                width_mm=1500, quantity_t=18, alternatives=[{"thickness_mm": 3.0}])


def inventory():
    inv = MemoryInventory()
    inv.add([lot("alt", thickness_mm=3.0), lot("far-off", thickness_mm=6.0), lot("unknown-name", grade_id=None, grade_text="ST37",
                                                                                  thickness_mm=2.5)])
    return inv


async def test_agent_finds_what_a_literal_search_misses(world, settings, kb):
    install(world, **{"sim/fast-commercial": {"follow_alternatives": 1.0, "search_variants": 1.0, "transient_error": 0.0}})
    inv = inventory()
    tools = LocalTools(inv, kb, settings.fit, TODAY)
    gw = Gateway.from_file()
    agent = mt.build_agent(gateway_model(gw, "match", "matcher"), "# matcher\nFind lots.")
    literal = await mt.match_line(SPEC, Policy(), tools, kb, settings, None)
    agentic = await mt.match_line(SPEC, Policy(), tools, kb, settings, agent)
    assert [f.item_id for f in literal.offers] == []
    assert [f.item_id for f in agentic.offers] == ["alt"]  # the alternative thickness the buyer named
    assert "unknown-name" in [f.item_id for f in agentic.review_offers] or agentic.offers
    assert agentic.agent_found == ["alt"]


async def test_budget_stops_the_loop_and_keeps_the_literal_result(world, settings, kb):
    install(world)
    raw = copy.deepcopy(settings.raw)
    raw["harness"]["max_model_requests"] = 1
    tight = Settings.from_dict(raw)
    tools = LocalTools(inventory(), kb, tight.fit, TODAY)
    agent = mt.build_agent(gateway_model(Gateway.from_file(), "match", "matcher"), "# matcher\nFind lots.")
    out = await mt.match_line(SPEC, Policy(), tools, kb, tight, agent)
    assert out.stop_reason == "budget"


async def test_model_outage_degrades_to_literal_search(world, settings, kb):
    install(world, **{m: {"transient_error": 1.0} for m in ["sim/fast-commercial", "sim/strong-commercial"]})
    tools = LocalTools(inventory(), kb, settings.fit, TODAY)
    agent = mt.build_agent(gateway_model(Gateway.from_file(), "match", "matcher"), "# matcher\nFind lots.")
    out = await mt.match_line(SPEC, Policy(), tools, kb, settings, agent)
    assert out.stop_reason == "model unavailable"
    assert any("unavailable" in r for r in out.reasons)


async def test_held_stock_list_is_not_half_loaded(world, settings, kb):
    install(world, **{m: {"mapping_swap": 1.0} for m in ["sim/open-weights-8b", "sim/strong-commercial"]})
    desk = desk_for(world, settings, kb)
    s = next(x for x in world.lists[0] if "length" in x.mapping["columns"] and "width" in x.mapping["columns"])
    res = await desk.import_stocklist(s.supplier_id, s.country, s.filename, s.data, "held", TODAY)
    assert res.status == "held" and res.items == [] and res.problems
    assert desk.inventory.items == {}
