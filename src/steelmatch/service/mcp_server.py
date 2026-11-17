"""The MCP server: the desk's tools for agents and outside clients, behind tokens and scopes.

Every call is checked against the caller's scopes, timed and logged (tool, client, outcome),
so the console can show who uses which tool and how often calls are denied or fail.
"""

from __future__ import annotations

import functools
import os
import time
from typing import Any, Callable

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

from ..domain.spec import LineSpec, Policy
from ..search.inventory import SearchQuery
from ..tools.base import fit_view, item_view
from .auth import RESOURCE, DeskTokenVerifier


class ScopeError(ToolError):
    """A denied call: the client sees why, the server logs it as 'denied'."""


def build(rt) -> MCPServer:
    server = MCPServer(
        "steel-offer-matcher",
        instructions="Tools of a steel trading desk: search supplier stock, resolve grade designations, check lots against a request, "
                     "read RFQs and their offers, record corrections.",
        token_verifier=DeskTokenVerifier(),
        auth=AuthSettings(issuer_url=os.environ.get("STEEL_ISSUER_URL", "http://localhost"), resource_server_url=RESOURCE,
                          required_scopes=[], validate_token_resource=True),
    )

    def guarded(scope: str) -> Callable:
        def deco(fn: Callable) -> Callable:
            @functools.wraps(fn)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                token = get_access_token()
                client = token.client_id if token else "anonymous"
                start = time.perf_counter()
                if token is None or scope not in token.scopes:
                    await rt.log_mcp(fn.__name__, client, "denied", 0.0, f"needs {scope}")
                    raise ScopeError(f"this client lacks the scope {scope}")
                try:
                    out = await fn(*args, **kwargs)
                except ToolError as e:
                    await rt.log_mcp(fn.__name__, client, "error", (time.perf_counter() - start) * 1000, str(e))
                    raise
                except Exception as e:
                    await rt.log_mcp(fn.__name__, client, "error", (time.perf_counter() - start) * 1000, f"{type(e).__name__}: {e}")
                    raise
                await rt.log_mcp(fn.__name__, client, "ok", (time.perf_counter() - start) * 1000)
                return out

            return wrapper

        return deco

    @server.tool()
    @guarded("inventory:read")
    async def search_inventory(text: str = "", finish: str | None = None, grade_ids: list[str] | None = None,
                               thickness_min_mm: float | None = None, thickness_max_mm: float | None = None,
                               width_min_mm: float | None = None, forms: list[str] | None = None,
                               include_unknown_designations: bool = False, limit: int = 20) -> dict:
        """Search active supplier stock. Hard filters apply first; text ranks by keywords and similarity."""
        q = SearchQuery(text=text, finish=finish, grade_ids=grade_ids or [], thickness_min_mm=thickness_min_mm,
                        thickness_max_mm=thickness_max_mm, width_min_mm=width_min_mm, forms=forms or [],
                        include_unresolved=include_unknown_designations, limit=min(limit, 50))
        return {"lots": [item_view(it) | {"thickness_mm": it.thickness_mm, "width_mm": it.width_mm}
                         for it in await rt.inventory.search(q)]}

    @server.tool()
    @guarded("inventory:read")
    async def get_lots(item_ids: list[str]) -> dict:
        """Full records of supplier lots by id."""
        return {"lots": [it.model_dump(mode="json") for it in await rt.inventory.items(item_ids[:50])]}

    @server.tool()
    @guarded("knowledge:read")
    async def resolve_grade(designation: str) -> dict:
        """Resolve a steel designation to a grade id; unknown designations return proposals, never a decision."""
        r = rt.desk.kb.resolve(designation)
        return {"grade_id": r.grade_id, "via": r.via, "coating": r.coating, "proposals": list(r.proposals), "note": r.note}

    @server.tool()
    @guarded("inventory:read")
    async def check_lots(requested: dict, item_ids: list[str], buyer_allows: dict | None = None) -> dict:
        """The code's verdict for lots against a requested line: status, landed EUR/t and every check."""
        from .search import PgTools

        spec = LineSpec.model_validate(requested)
        fits = await PgTools(rt.inventory, rt.desk.kb, rt.settings.fit, rt.today()).fits(spec, Policy.model_validate(buyer_allows or {}),
                                                                                      item_ids[:20])
        return {"fits": [fit_view(f) | {"checks": [c.__dict__ for c in f.checks], "partial": f.partial, "lead_days": f.lead_days}
                         for f in fits]}

    @server.tool()
    @guarded("rfq:read")
    async def get_rfq(message_id: str) -> dict:
        """An RFQ with its extracted lines, evidence, review items and ranked offers."""
        r = await rt.get_rfq(message_id)
        if r is None:
            raise ToolError(f"no RFQ {message_id}")
        return r

    @server.tool()
    @guarded("rfq:review")
    async def record_correction(message_id: str, line: int, field: str, value: str) -> dict:
        """Correct an extracted value. The correction becomes an eval case for the gate."""
        token = get_access_token()
        return await rt.correct(message_id, line, field, value, by=token.client_id if token else "unknown")

    return server


def asgi(server: MCPServer, hosts: list[str]):
    return server.streamable_http_app(
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=hosts + [f"{h}:*" for h in hosts],
            allowed_origins=[f"{p}://{h}{port}" for p in ("http", "https") for h in hosts for port in ("", ":*")]),
    )
