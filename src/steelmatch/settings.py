"""Platform settings, read from config/matcher.json. Nothing here comes from the world."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .domain.fit import FitSettings

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class HarnessSettings:
    max_model_requests: int
    max_tool_calls: int
    max_input_tokens: int
    stop_after_searches_without_new: int
    tool_result_keep: int
    model_retries: int


@dataclass(frozen=True)
class Settings:
    fit: FitSettings
    harness: HarnessSettings
    required_fields: tuple[str, ...]
    escalate_on_invalid_fields: int
    offers_per_line: int
    search_candidates: int
    code_check_top_n: int
    rrf_k: int
    routes: dict[str, str]
    alias_min_corrections: int
    explain_language: str
    raw: dict

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        return cls(
            fit=FitSettings.from_dict(d["fit"]),
            harness=HarnessSettings(**d["harness"]),
            required_fields=tuple(d["extraction"]["required_fields"]),
            escalate_on_invalid_fields=d["extraction"]["escalate_on_invalid_fields"],
            offers_per_line=d["matching"]["offers_per_line"],
            search_candidates=d["matching"]["search_candidates"],
            code_check_top_n=d["matching"]["code_check_top_n"],
            rrf_k=d["matching"]["rrf_k"],
            routes=dict(d["routes"]),
            alias_min_corrections=d["learning"]["alias_min_corrections"],
            explain_language=d["review"]["explain_language"],
            raw=d,
        )


@lru_cache(maxsize=1)
def load(path: str | None = None) -> Settings:
    p = Path(path) if path else ROOT / "config" / "matcher.json"
    return Settings.from_dict(json.loads(p.read_text()))
