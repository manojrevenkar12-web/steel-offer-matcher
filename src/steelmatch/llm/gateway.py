"""LiteLLM as the model gateway.

Agents never name a model. They call a route ("extract", "match", ...); the gateway maps the
route to a deployment, retries, falls back to the next route when a deployment fails, and
books tokens, cost and latency per call so model choice can be judged on accuracy, latency
and cost together.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import litellm
from litellm import Router

ROOT = Path(__file__).resolve().parents[3]
litellm.suppress_debug_info = True
litellm.telemetry = False


@dataclass
class CallRecord:
    route: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_eur: float
    latency_ms: float
    ok: bool
    fallback: bool
    error: str = ""
    metadata: dict = field(default_factory=dict)


class GatewayError(RuntimeError):
    pass


class Gateway:
    def __init__(self, config: dict, clock: Callable[[], float] | None = None):
        self.config = config
        self.prices = {d["model"]: (d["price_in"], d["price_out"]) for d in config["deployments"]}
        self.route_of = {}
        model_list = []
        for d in config["deployments"]:
            params = {"model": d["model"], "timeout": d.get("timeout_s", 30)}
            params.update(d.get("litellm_params", {}))
            model_list.append({"model_name": d["route"], "litellm_params": params})
            self.route_of[d["model"]] = d["route"]
        self.router = Router(
            model_list=model_list,
            fallbacks=[{k: v} for k, v in config.get("fallbacks", {}).items()],
            num_retries=config.get("num_retries", 2),
            retry_after=config.get("retry_after_s", 0),
        )
        self.records: list[CallRecord] = []
        self.listeners: list[Callable[[CallRecord], None]] = []
        self._clock = clock or time.perf_counter

    @classmethod
    def from_file(cls, path: Path | None = None) -> "Gateway":
        return cls(json.loads((path or ROOT / "config" / "models.json").read_text()))

    async def complete(self, route: str, messages: list[dict], tools: list[dict] | None = None,
                       metadata: dict | None = None) -> Any:
        start = self._clock()
        kwargs: dict[str, Any] = {"model": route, "messages": messages}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "required"
        if metadata:
            kwargs["metadata"] = metadata
        try:
            resp = await self.router.acompletion(**kwargs)
        except Exception as e:  # every deployment and fallback failed
            rec = CallRecord(route, "", 0, 0, 0.0, (self._clock() - start) * 1000, False, False, type(e).__name__, metadata or {})
            self._book(rec)
            raise GatewayError(f"route {route}: {type(e).__name__}: {e}") from e
        model = (getattr(resp, "_hidden_params", {}) or {}).get("litellm_model_name") or resp.model or ""
        model = self._deployment(model)
        usage = getattr(resp, "usage", None)
        tin = int(getattr(usage, "prompt_tokens", 0) or 0)
        tout = int(getattr(usage, "completion_tokens", 0) or 0)
        pin, pout = self.prices.get(model, (0.0, 0.0))
        cost = (tin * pin + tout * pout) / 1_000_000
        sim_latency = (getattr(resp, "_hidden_params", {}) or {}).get("sim_latency_ms")
        latency = float(sim_latency) if sim_latency is not None else (self._clock() - start) * 1000
        headers = (getattr(resp, "_hidden_params", {}) or {}).get("additional_headers", {}) or {}
        served_by = headers.get("x-litellm-model-group", route)
        fell_back = served_by != route or int(headers.get("x-litellm-attempted-fallbacks", 0) or 0) > 0
        rec = CallRecord(route, model, tin, tout, cost, latency, True, fell_back, "", metadata or {})
        self._book(rec)
        return resp

    def _deployment(self, name: str) -> str:
        if name in self.prices:
            return name
        for m in self.prices:
            if m.split("/", 1)[-1] == name or name.endswith(m.split("/", 1)[-1]):
                return m
        return name

    def _book(self, rec: CallRecord) -> None:
        self.records.append(rec)
        for f in self.listeners:
            f(rec)

    def summary(self) -> dict:
        out: dict[str, dict] = {}
        for r in self.records:
            k = out.setdefault(r.model or "(failed)", {"calls": 0, "errors": 0, "fallbacks": 0, "cost_eur": 0.0, "latency_ms": []})
            k["calls"] += 1
            k["errors"] += 0 if r.ok else 1
            k["fallbacks"] += 1 if r.fallback else 0
            k["cost_eur"] += r.cost_eur
            k["latency_ms"].append(r.latency_ms)
        for k in out.values():
            lat = sorted(k.pop("latency_ms"))
            k["p50_ms"] = lat[len(lat) // 2] if lat else 0
            k["p95_ms"] = lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else 0
            k["cost_eur"] = round(k["cost_eur"], 6)
        return out
