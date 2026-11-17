"""Bearer tokens for the API and the MCP server, with scopes per client."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from mcp.server.auth.provider import AccessToken

ROOT = Path(__file__).resolve().parents[3]
RESOURCE = os.environ.get("STEEL_MCP_RESOURCE", "http://localhost/mcp")


@dataclass(frozen=True)
class Client:
    client_id: str
    scopes: tuple[str, ...]


@lru_cache(maxsize=4)
def _clients(path: str) -> dict[str, Client]:
    data = json.loads(Path(path).read_text())
    return {c["token_sha256"]: Client(c["client_id"], tuple(c["scopes"])) for c in data["clients"]}


def clients() -> dict[str, Client]:
    return _clients(os.environ.get("STEEL_DESKS_FILE", str(ROOT / "config" / "desks.json")))


def lookup(token: str | None) -> Client | None:
    if not token:
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    for known, client in clients().items():
        if hmac.compare_digest(known, digest):
            return client
    return None


class DeskTokenVerifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        c = lookup(token)
        if c is None:
            return None
        return AccessToken(token=token, client_id=c.client_id, scopes=list(c.scopes), resource=RESOURCE)
