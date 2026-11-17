"""The matching agent and the harness around it.

The harness runs a literal structured search first (the requested grade, the thickness band,
at least the requested width). The agent then searches for what a literal filter misses:
alternatives the buyer named, lots whose designation the supplier wrote differently, higher
grades when the buyer allows them. It sees the code's verdict through `check_fit`, never makes
one. The harness bounds the loop (requests, tool calls, input tokens, searches that find
nothing new), retries model failures, and falls back to the literal search when the model is
unavailable. Code checks every candidate and ranks; the agent only widens the net.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.usage import UsageLimits

from ..domain.fit import Fit, rank, thickness_band
from ..domain.grades import GradeKB
from ..domain.spec import LineSpec, Policy
from ..llm.gateway import GatewayError
from ..search.inventory import SearchQuery
from ..settings import Settings
from ..tools.base import MatchTools, fit_view, item_view


class Pick(BaseModel):
    item_id: str
    reason: str = ""


class MatchProposal(BaseModel):
    picks: list[Pick] = Field(default_factory=list)
    note: str = ""


@dataclass
class MatchState:
    spec: LineSpec
    policy: Policy
    tools: MatchTools
    kb: GradeKB
    seen: set[str] = field(default_factory=set)
    agent_found: set[str] = field(default_factory=set)
    searches: int = 0
    searches_without_new: int = 0
    tool_calls: int = 0
    stop_after: int = 2
    max_tool_calls: int = 12
    keep: int = 6
    log: list[dict] = field(default_factory=list)

    @property
    def should_stop(self) -> bool:
        return self.searches_without_new >= self.stop_after or self.tool_calls >= self.max_tool_calls


def build_agent(model, instructions: str, retries: int = 1) -> Agent[MatchState, MatchProposal]:
    agent: Agent[MatchState, MatchProposal] = Agent(model, output_type=MatchProposal, instructions=instructions,
                                                    deps_type=MatchState, retries=retries, name="matcher")

    @agent.tool
    async def search_inventory(ctx: RunContext[MatchState], text: str = "", grade_ids: list[str] | None = None,
                               thickness_mm: float | None = None, width_min_mm: float | None = None,
                               forms: list[str] | None = None, include_unknown_designations: bool = False) -> dict:
        """Search supplier stock. The finish of the requested line always applies. Returns compact lots."""
        st = ctx.deps
        st.tool_calls += 1
        if st.should_stop:
            return {"stop": True, "message": "Searches are finding nothing new. Return your picks now."}
        band = thickness_band(thickness_mm, st.spec.finish) if thickness_mm else None
        q = SearchQuery(text=text, finish=st.spec.finish, forms=forms or [], grade_ids=grade_ids or [],
                        include_unresolved=include_unknown_designations,
                        thickness_min_mm=(thickness_mm - band) if thickness_mm else None,
                        thickness_max_mm=(thickness_mm + band) if thickness_mm else None,
                        width_min_mm=width_min_mm, limit=st.keep)
        found = await st.tools.search(q)
        new = [it.item_id for it in found if it.item_id not in st.seen]
        st.seen.update(new)
        st.agent_found.update(new)
        st.searches += 1
        st.searches_without_new = 0 if new else st.searches_without_new + 1
        st.log.append({"tool": "search_inventory", "query": q.model_dump(exclude_defaults=True), "new": len(new)})
        return {"lots": [item_view(it) for it in found], "new": len(new)}

    @agent.tool
    async def check_fit(ctx: RunContext[MatchState], item_ids: list[str]) -> list[dict]:
        """The code's verdict for lots against the requested line: status, landed EUR/t, issues."""
        st = ctx.deps
        st.tool_calls += 1
        fits = await st.tools.fits(st.spec, st.policy, item_ids[: st.keep])
        st.log.append({"tool": "check_fit", "items": len(fits)})
        return [fit_view(f) for f in fits]

    @agent.tool
    def resolve_grade(ctx: RunContext[MatchState], designation: str) -> dict:
        """Resolve a designation to a grade id, or get proposals when it is unknown."""
        r = ctx.deps.kb.resolve(designation)
        return {"grade_id": r.grade_id, "via": r.via, "proposals": list(r.proposals)}

    return agent


@dataclass
class LineOutcome:
    line: int
    offers: list[Fit]
    review_offers: list[Fit]
    considered: int
    agent_found: list[str]
    stop_reason: str
    needs_review: bool
    reasons: list[str]
    log: list[dict]
    notes: list[str] = field(default_factory=list)


def literal_query(spec: LineSpec, limit: int) -> SearchQuery:
    band = thickness_band(spec.thickness_mm, spec.finish)
    forms = ["coil", "strip", "sheet", "plate"] if spec.form in ("sheet", "plate") else ["coil", "strip"]
    return SearchQuery(text=f"{spec.grade_id} {spec.grade_text} {spec.thickness_mm:g}x{spec.width_mm:g}",
                       finish=spec.finish, forms=forms, grade_ids=[spec.grade_id],
                       thickness_min_mm=spec.thickness_mm - band, thickness_max_mm=spec.thickness_mm + band,
                       width_min_mm=min([spec.width_mm] + [a["width_min_mm"] for a in spec.alternatives if "width_min_mm" in a]),
                       limit=limit)


def prompt_for(spec: LineSpec, policy: Policy, base: list[Fit]) -> str:
    payload = {
        "requested": spec.model_dump(mode="json", exclude_none=True),
        "buyer_allows": policy.model_dump(mode="json"),
        "first_search": [fit_view(f) for f in base[:5]],
    }
    return "Find supplier lots for this line.\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"


async def match_line(spec: LineSpec, policy: Policy, tools: MatchTools, kb: GradeKB, settings: Settings,
                     agent: Agent[MatchState, MatchProposal] | None) -> LineOutcome:
    h = settings.harness
    base_items = await tools.search(literal_query(spec, settings.search_candidates))
    base_ids = [it.item_id for it in base_items][: settings.code_check_top_n]
    base_fits = rank(await tools.fits(spec, policy, base_ids))

    state = MatchState(spec, policy, tools, kb, seen=set(base_ids), stop_after=h.stop_after_searches_without_new,
                       max_tool_calls=h.max_tool_calls, keep=h.tool_result_keep)
    stop_reason = "no agent"
    picks: list[str] = []
    if agent is not None:
        stop_reason = "proposal"
        for attempt in range(h.model_retries + 1):
            try:
                res = await agent.run(prompt_for(spec, policy, base_fits), deps=state,
                                      usage_limits=UsageLimits(request_limit=h.max_model_requests,
                                                               input_tokens_limit=h.max_input_tokens))
                picks = [p.item_id for p in res.output.picks]
                if state.should_stop:
                    stop_reason = "no new results"
                break
            except UsageLimitExceeded:
                stop_reason = "budget"
                break
            except GatewayError:
                stop_reason = "model unavailable"
                continue
            except Exception as e:  # invalid output after Pydantic AI's own retries
                stop_reason = f"agent error: {type(e).__name__}"
                break

    extra = [i for i in dict.fromkeys(list(state.agent_found) + picks) if i not in base_ids]
    extra_fits = await tools.fits(spec, policy, extra) if extra else []
    ranked = rank(base_fits + extra_fits)
    offers = [f for f in ranked if f.status == "pass"][: settings.offers_per_line]
    review_offers = [f for f in ranked if f.status == "review"][: settings.offers_per_line] if not offers else []
    reasons: list[str] = []  # decisions a person has to make
    notes: list[str] = []  # facts the offer text states, no decision needed
    if not offers and not review_offers:
        notes.append("no lot fits")
    if review_offers:
        reasons.append("best lots need a decision: " + "; ".join(review_offers[0].explain()))
    if offers and offers[0].partial:
        notes.append("best lot covers only part of the quantity")
    if stop_reason == "model unavailable":
        reasons.append("matching agent unavailable; literal search only")
    agent_only = [f.item_id for f in offers + review_offers if f.item_id in extra]
    return LineOutcome(spec.line, offers, review_offers, len(base_ids) + len(extra), agent_only, stop_reason,
                       bool(reasons), reasons, state.log, notes)
