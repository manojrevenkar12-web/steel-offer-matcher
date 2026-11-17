"""The tools agents use, behind one interface with two transports.

LocalTools calls the inventory and the fit rules in-process (used by batch runs and tests).
McpTools (tools/mcp_client.py) calls the same tools on the MCP server, with the desk's token,
so agents and outside clients go through one audited surface. Both return the same shapes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from ..domain.fit import Fit, FitSettings, check
from ..domain.grades import GradeKB
from ..domain.spec import LineSpec, Policy, StockItem
from ..search.inventory import MemoryInventory, SearchQuery


def item_view(it: StockItem) -> dict:
    dims = f"{it.thickness_mm:g} x {it.width_mm:g}" + (f" x {it.length_mm:g}" if it.length_mm else " x C")
    return {"item_id": it.item_id, "grade": it.grade_id or f"? {it.grade_text}", "designation": it.grade_text,
            "form": it.form, "dims_mm": dims, "coating": it.coating, "weight_t": it.weight_t,
            "list_price_eur_t": it.price_eur_t, "country": it.country, "quality": it.quality}


def fit_view(f: Fit) -> dict:
    return {"item_id": f.item_id, "status": f.status, "landed_eur_t": f.landed_eur_t, "usable_t": f.usable_t,
            "processing": f.processing, "issues": f.explain() if f.status != "pass" else [],
            "grade_match": f.grade_match}


class MatchTools(Protocol):
    async def search(self, q: SearchQuery) -> list[StockItem]: ...
    async def items(self, item_ids: list[str]) -> list[StockItem]: ...
    async def fits(self, spec: LineSpec, policy: Policy, item_ids: list[str]) -> list[Fit]: ...


@dataclass
class LocalTools:
    inventory: MemoryInventory
    kb: GradeKB
    fit_settings: FitSettings
    today: date

    async def search(self, q: SearchQuery) -> list[StockItem]:
        return self.inventory.search(q)

    async def items(self, item_ids: list[str]) -> list[StockItem]:
        return [it for i in item_ids if (it := self.inventory.get(i)) is not None]

    async def fits(self, spec: LineSpec, policy: Policy, item_ids: list[str]) -> list[Fit]:
        return [check(spec, policy, it, self.kb, self.fit_settings, self.today) for it in await self.items(item_ids)]
