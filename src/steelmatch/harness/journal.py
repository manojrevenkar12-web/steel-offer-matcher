"""Workflow state: every finished step of a run is journaled, so a restarted run resumes.

A step's result is stored under (run id, step name). Running the same step again returns the
stored result instead of calling the model again, which makes retries after a crash cheap and
keeps a run's model spend from doubling.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol


class JournalStore(Protocol):
    def get(self, run_id: str, step: str) -> Any | None: ...
    def put(self, run_id: str, step: str, value: Any) -> None: ...
    def steps(self, run_id: str) -> list[tuple[str, Any]]: ...


@dataclass
class MemoryJournal:
    data: dict[str, dict[str, str]] = field(default_factory=dict)
    replays: int = 0

    def get(self, run_id: str, step: str) -> Any | None:
        raw = self.data.get(run_id, {}).get(step)
        return None if raw is None else json.loads(raw)

    def put(self, run_id: str, step: str, value: Any) -> None:
        self.data.setdefault(run_id, {})[step] = json.dumps(value, default=str)

    def steps(self, run_id: str) -> list[tuple[str, Any]]:
        return [(k, json.loads(v)) for k, v in self.data.get(run_id, {}).items()]


async def _maybe(x: Any) -> Any:
    return await x if inspect.isawaitable(x) else x


async def step(journal: JournalStore, run_id: str, name: str, fn: Callable[[], Awaitable[Any]]) -> Any:
    stored = await _maybe(journal.get(run_id, name))
    if stored is not None:
        if hasattr(journal, "replays"):
            journal.replays += 1
        return stored
    value = await fn()
    if value is not None:  # a failed step is not journaled, so a retry runs it again
        await _maybe(journal.put(run_id, name, value))
    return value
