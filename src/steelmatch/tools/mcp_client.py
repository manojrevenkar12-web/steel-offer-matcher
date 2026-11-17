"""The agents' tools over MCP: the same interface as LocalTools, through the desk's MCP server.

Used when agents run outside the service (a batch job, another team's agent) so every tool
call passes the same token check, scope check and call log as any other client.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from typing import AsyncIterator

from ..domain.fit import Check, Fit
from ..domain.spec import LineSpec, Policy, StockItem
from ..search.inventory import SearchQuery


@dataclass
class McpTools:
    client: object

    @classmethod
    @contextlib.asynccontextmanager
    async def connect(cls, url: str, token: str) -> AsyncIterator["McpTools"]:
        import httpx2
        from mcp.client.client import Client
        from mcp.client.streamable_http import streamable_http_client

        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=60) as hc:
            async with Client(streamable_http_client(url, http_client=hc)) as c:
                yield cls(c)

    async def _call(self, name: str, args: dict) -> dict:
        res = await self.client.call_tool(name, args)
        text = res.content[0].text if res.content else "{}"
        if res.is_error:
            raise PermissionError(text) if "scope" in text else RuntimeError(text)
        return json.loads(text)

    async def search(self, q: SearchQuery) -> list[StockItem]:
        out = await self._call("search_inventory", {
            "text": q.text, "finish": q.finish, "grade_ids": q.grade_ids, "thickness_min_mm": q.thickness_min_mm,
            "thickness_max_mm": q.thickness_max_mm, "width_min_mm": q.width_min_mm, "forms": q.forms,
            "include_unknown_designations": q.include_unresolved, "limit": q.limit})
        ids = [l["item_id"] for l in out["lots"]]
        return await self.items(ids)

    async def items(self, item_ids: list[str]) -> list[StockItem]:
        if not item_ids:
            return []
        out = await self._call("get_lots", {"item_ids": item_ids})
        return [StockItem.model_validate(x) for x in out["lots"]]

    async def fits(self, spec: LineSpec, policy: Policy, item_ids: list[str]) -> list[Fit]:
        if not item_ids:
            return []
        out = await self._call("check_lots", {"requested": spec.model_dump(mode="json"), "item_ids": item_ids,
                                              "buyer_allows": policy.model_dump(mode="json")})
        return [Fit(item_id=f["item_id"], status=f["status"], checks=[Check(**c) for c in f["checks"]], usable_t=f["usable_t"],
                    landed_eur_t=f["landed_eur_t"], processing=f["processing"], grade_match=f["grade_match"],
                    partial=f.get("partial", False), lead_days=f.get("lead_days", 0)) for f in out["fits"]]
