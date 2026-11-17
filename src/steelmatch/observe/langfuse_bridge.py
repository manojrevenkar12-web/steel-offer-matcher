"""Langfuse: prompt versions, traces of every agent run, datasets built from corrections.

Prompts live in prompts/*.md in the repository and are pushed to Langfuse as new versions with
the label 'staging'. Only the evaluation gate moves a version to 'production'. At run time the
agents read the 'production' version from Langfuse and fall back to the repository copy when
Langfuse is not configured or not reachable, so an outage never stops the desk.
"""

from __future__ import annotations

import hashlib
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Prompt:
    name: str
    text: str
    version: str
    source: str  # "langfuse" | "local"


def local_prompt(name: str) -> Prompt:
    text = (ROOT / "prompts" / f"{name}.md").read_text()
    return Prompt(name, text, "local-" + hashlib.sha256(text.encode()).hexdigest()[:8], "local")


class Observer:
    """Thin wrapper over the Langfuse client. With no client, every call is a no-op."""

    def __init__(self, client: Any | None = None, label: str = "production"):
        self.client = client
        self.label = label
        self._prompt_cache: dict[str, Prompt] = {}

    @classmethod
    def from_env(cls) -> "Observer":
        if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
            return cls(None)
        from langfuse import Langfuse

        return cls(Langfuse())

    @property
    def enabled(self) -> bool:
        return self.client is not None

    # Prompts -----------------------------------------------------------------------------
    def prompt(self, name: str) -> Prompt:
        if name in self._prompt_cache:
            return self._prompt_cache[name]
        local = local_prompt(name)
        if not self.client:
            self._prompt_cache[name] = local
            return local
        try:
            p = self.client.get_prompt(name, label=self.label, fallback=local.text, max_retries=0, fetch_timeout_seconds=2)
            is_fallback = getattr(p, "is_fallback", False)
            out = local if is_fallback else Prompt(name, p.prompt, f"lf-{p.version}", "langfuse")
        except Exception:
            out = local
        self._prompt_cache[name] = out
        return out

    def push_prompts(self, names: list[str], label: str = "staging") -> list[str]:
        """Create a new Langfuse version for each local prompt; returns the names pushed."""
        if not self.client:
            return []
        pushed = []
        for name in names:
            local = local_prompt(name)
            self.client.create_prompt(name=name, prompt=local.text, labels=[label], type="text",
                                      commit_message=f"from repository {local.version}")
            pushed.append(name)
        return pushed

    # Traces ------------------------------------------------------------------------------
    @contextmanager
    def span(self, name: str, as_type: str = "span", **kwargs: Any) -> Iterator[Any]:
        if not self.client:
            yield _Noop()
            return
        with self.client.start_as_current_observation(name=name, as_type=as_type, **kwargs) as obs:
            yield obs

    def generation(self, name: str, model: str, input_tokens: int, output_tokens: int, cost_eur: float,
                   metadata: dict | None = None, ok: bool = True) -> None:
        if not self.client:
            return
        g = self.client.start_observation(name=name, as_type="generation", model=model,
                                          usage_details={"input": input_tokens, "output": output_tokens},
                                          cost_details={"total": cost_eur}, metadata=metadata or {},
                                          level="DEFAULT" if ok else "ERROR")
        g.end()

    def correction(self, trace_id: str | None, field: str, before: Any, after: Any, comment: str = "") -> None:
        if not self.client or not trace_id:
            return
        self.client.create_score(name=f"correction.{field}", value=f"{before} -> {after}", trace_id=trace_id,
                                 data_type="CORRECTION", comment=comment)

    # Datasets ----------------------------------------------------------------------------
    def add_case(self, dataset: str, case_id: str, input: dict, expected: dict, metadata: dict | None = None,
                 trace_id: str | None = None) -> None:
        if not self.client:
            return
        self.client.create_dataset_item(dataset_name=dataset, id=case_id, input=input, expected_output=expected,
                                        metadata=metadata or {}, source_trace_id=trace_id)

    def flush(self) -> None:
        if self.client:
            self.client.flush()


class _Noop:
    id = None
    trace_id = None

    def update(self, **_: Any) -> None:
        pass
