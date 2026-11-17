"""Simulated language models behind LiteLLM's custom-provider hook.

Each simulated model reads what a real one would get (the prompt, the tool definitions, the
tool results) and answers in OpenAI tool-call format. Where a real model would read the
document, the simulation starts from what a perfect reader would return (recorded by the world
while rendering) and adds the mistakes listed for that model in world.json: missed fields,
misread numbers, paraphrased quotes, invented values, over-read policy sentences. The same
seed, document and step give the same mistakes in every design.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field

import litellm
from litellm import CustomLLM, ModelResponse

from . import rand
from .catalog import BY_ID, MATERIALS, can_replace


@dataclass
class Registry:
    rfqs: dict[str, dict] = field(default_factory=dict)  # sha256(body) -> claims
    layouts: dict[tuple, dict] = field(default_factory=dict)  # header tuple -> mapping
    images: dict[str, str] = field(default_factory=dict)  # sha256(png) -> text


def body_key(body: str) -> str:
    return hashlib.sha256(body.strip().encode()).hexdigest()


def _tokens(x) -> int:
    return max(1, len(json.dumps(x, ensure_ascii=False, default=str)) // 4)


def _text_of(messages: list[dict]) -> str:
    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            out.append(c)
        elif isinstance(c, list):
            out.extend(p.get("text", "") for p in c if isinstance(p, dict))
    return "\n".join(out)


class SimLLM(CustomLLM):
    def __init__(self, seed: int, registry: Registry, profiles: dict, label: str = ""):
        super().__init__()
        self.seed = seed
        self.reg = registry
        self.profiles = profiles
        self.label = label
        self.attempts: dict[tuple, int] = {}
        self.calls = 0

    # LiteLLM entry points ---------------------------------------------------------------------
    def completion(self, *args, **kwargs):  # pragma: no cover - the platform is async
        raise NotImplementedError

    async def acompletion(self, *args, **kwargs) -> ModelResponse:
        model = "sim/" + kwargs.get("model", "")
        messages = kwargs.get("messages") or []
        tools = (kwargs.get("optional_params") or {}).get("tools") or kwargs.get("tools") or []
        prof = self.profiles[model]
        system = next((m["content"] for m in messages if m.get("role") == "system" and isinstance(m.get("content"), str)), "")
        agent = (re.match(r"#\s*(\w+)", system) or [None, "unknown"])[1]
        user = next((m for m in messages if m.get("role") == "user"), {"content": ""})
        key = (agent, hashlib.sha256(json.dumps(user.get("content"), default=str).encode()).hexdigest()[:16], len(messages), model)
        n = self.attempts.get(key, 0)
        self.attempts[key] = n + 1
        self.calls += 1
        r = rand.of(self.seed, "llm", *key, n)
        if r.random() < prof.get("transient_error", 0.0):
            raise litellm.RateLimitError(message="simulated 429", llm_provider="sim", model=model)
        out_tool = next((t["function"]["name"] for t in tools if t["function"]["name"] == "final_result"), None)
        if agent == "extractor":
            call = self._extract(messages, prof, r, out_tool)
        elif agent == "matcher":
            call = self._match(messages, prof, r)
        elif agent == "stocklist_mapper":
            call = self._map(messages, prof, r)
        elif agent == "picker":
            call = self._pick(messages, prof, r)
        elif agent == "transcriber":
            call = ("text", self._transcribe(messages, prof, r))
        else:
            call = ("text", "")
        lat = r.uniform(*prof.get("latency_ms", [500, 1500]))
        tin = _tokens(messages) + _tokens(tools)
        if call[0] == "text":
            msg = {"role": "assistant", "content": call[1]}
            tout = _tokens(call[1])
            finish = "stop"
        else:
            _, name, args = call
            msg = {"role": "assistant", "content": None,
                   "tool_calls": [{"id": "call_" + uuid.UUID(int=r.getrandbits(128)).hex[:12], "type": "function",
                                   "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}
            tout = _tokens(args)
            finish = "tool_calls"
        resp = ModelResponse(model=model, choices=[{"message": msg, "finish_reason": finish, "index": 0}],
                             usage={"prompt_tokens": tin, "completion_tokens": tout, "total_tokens": tin + tout})
        resp._hidden_params["sim_latency_ms"] = lat
        return resp

    # Extraction ---------------------------------------------------------------------------------
    def _extract(self, messages, prof, r, out_tool):
        prompt = _text_of([m for m in messages if m.get("role") == "user"])
        m = re.search(r"Subject: [^\n]*\n\n(.*?)(?:\n\[[^\]\n]+\]\n|\Z)", prompt, re.S)
        truth = self.reg.rfqs.get(body_key(m.group(1))) if m else None
        if truth is None:
            return ("tool", out_tool, {"is_rfq": False, "language": "other", "lines": []})
        tool_msgs = [x for x in messages if x.get("role") == "tool"]
        if truth.get("is_rfq") and not tool_msgs and r.random() < 0.3 and truth["lines"]:
            return ("tool", "lookup_grade", {"designation": truth["lines"][0]["grade"]})
        return ("tool", out_tool, noisy_claims(truth, prof, r))

    # Matching ------------------------------------------------------------------------------------
    def _match(self, messages, prof, r):
        prompt = _text_of([m for m in messages if m.get("role") == "user"])
        m = re.search(r"```json\n(.*?)\n```", prompt, re.S)
        data = json.loads(m.group(1)) if m else {}
        req, allows = data.get("requested", {}), data.get("buyer_allows", {})
        plan: list[dict] = []
        grade = req.get("grade_id")
        width_min = min([req.get("width_mm", 0)] + [a["width_min_mm"] for a in req.get("alternatives", []) if "width_min_mm" in a])
        for a in req.get("alternatives", []):
            if "thickness_mm" in a and r.random() < prof["follow_alternatives"]:
                plan.append({"grade_ids": [grade], "thickness_mm": a["thickness_mm"], "width_min_mm": width_min,
                             "text": f"{grade} {a['thickness_mm']}"})
        if r.random() < prof["search_variants"]:
            plan.append({"text": f"{req.get('grade_text') or grade} {grade} {req.get('thickness_mm')}x{req.get('width_mm')}",
                         "thickness_mm": req.get("thickness_mm"), "width_min_mm": width_min, "include_unknown_designations": True})
        if allows.get("upgrades_ok") and grade in BY_ID and r.random() < prof["follow_alternatives"]:
            ups = [x.id for x in MATERIALS if x.id != grade and can_replace(grade, x.id)]
            if ups:
                plan.append({"grade_ids": ups, "thickness_mm": req.get("thickness_mm"), "width_min_mm": width_min,
                             "text": " ".join(ups)})
        done_search = 0
        found: list[str] = []
        checked: dict[str, str] = {}
        stop = False
        for x in messages:
            if x.get("role") == "assistant":
                for tc in x.get("tool_calls") or []:
                    if tc["function"]["name"] == "search_inventory":
                        done_search += 1
            if x.get("role") == "tool":
                try:
                    payload = json.loads(x["content"])
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(payload, dict) and payload.get("stop"):
                    stop = True
                if isinstance(payload, dict):
                    found.extend(l["item_id"] for l in payload.get("lots", []))
                if isinstance(payload, list):
                    for f in payload:
                        checked[f["item_id"]] = f["status"]
        for f in data.get("first_search", []):
            checked.setdefault(f["item_id"], f["status"])
        unchecked = [i for i in dict.fromkeys(found) if i not in checked]
        if unchecked and not stop:
            return ("tool", "check_fit", {"item_ids": unchecked[:6]})
        if done_search < len(plan) and not stop:
            return ("tool", "search_inventory", plan[done_search])
        picks = [{"item_id": i, "reason": "fits"} for i, s in checked.items() if s == "pass"][:5]
        picks += [{"item_id": i, "reason": "needs a decision"} for i, s in checked.items() if s == "review"][:2]
        return ("tool", "final_result", {"picks": picks, "note": ""})

    # Stock-list mapping ----------------------------------------------------------------------------
    def _map(self, messages, prof, r):
        prompt = _text_of([m for m in messages if m.get("role") == "user"])
        lines = prompt.split("List:\n", 1)[-1].splitlines()
        headers = tuple(h.strip() for h in lines[0].split(" | ")) if lines else ()
        truth = self.reg.layouts.get(headers)
        if truth is None:
            return ("tool", "final_result", {"columns": {}})
        out = copy.deepcopy(truth)
        cols = out["columns"]
        if r.random() < prof.get("mapping_swap", 0.02) and "width" in cols and "length" in cols:
            cols["width"], cols["length"] = cols["length"], cols["width"]
        if r.random() < prof.get("mapping_unit", 0.015):
            out["weight_unit"] = "t" if out["weight_unit"] == "kg" else "kg"
        return ("tool", "final_result", out)

    # Picking without code checks (the model-decides design) --------------------------------------
    def _pick(self, messages, prof, r):
        """A model choosing lots by reading them: every check is done, but each one can be skipped or misjudged."""
        prompt = _text_of([m for m in messages if m.get("role") == "user"])
        m = re.search(r"```json\n(.*?)\n```", prompt, re.S)
        data = json.loads(m.group(1)) if m else {}
        req, allows = data.get("requested", {}), data.get("buyer_allows", {})
        loose = prof.get("decide_loose", 0.25)
        careful = lambda: r.random() > loose
        certs = {"none": 0, "2.2": 1, "3.1": 2, "3.2": 3}
        picks = []
        for lot in data.get("lots", []):
            t = req.get("thickness_mm") or 0
            if abs(lot["thickness_mm"] - t) > (0.2 if careful() else 0.5):
                continue
            if lot["width_mm"] < (req.get("width_mm") or 0) and careful():
                continue
            g = req.get("grade") or ""
            if careful():
                if _material_of(lot["grade"]) != _material_of(g) or _material_of(g) is None:
                    continue
            elif _gnorm(lot["grade"])[:4] != _gnorm(g)[:4]:
                continue
            if careful() and req.get("form") in ("coil", "strip") and lot["form"] in ("sheet", "plate"):
                continue
            if careful() and lot["quality"] == "secondary" and not allows.get("secondary_ok"):
                continue
            if careful() and certs.get(lot["certificate"], 0) < certs.get(allows.get("certificate", "none"), 0):
                continue
            if careful() and (req.get("coating") or lot.get("coating")) and req.get("coating") != lot.get("coating"):
                continue
            if careful() and lot.get("origin") in (allows.get("excluded_origins") or []):
                continue
            price = lot["price_eur_t"] + (30 if r.random() < 0.5 else 45)
            if careful() and req.get("max_price_eur_t") and price > req["max_price_eur_t"]:
                continue
            picks.append({"item_id": lot["item_id"], "price_eur_t": round(price, 0)})
        picks.sort(key=lambda p: p["price_eur_t"])
        return ("tool", "final_result", {"picks": picks[:3]})

    # Vision ----------------------------------------------------------------------------------------
    def _transcribe(self, messages, prof, r) -> str:
        for m in messages:
            c = m.get("content")
            if isinstance(c, list):
                for p in c:
                    if isinstance(p, dict) and p.get("type") == "image_url":
                        data = base64.b64decode(p["image_url"]["url"].split(",", 1)[1])
                        text = self.reg.images.get(hashlib.sha256(data).hexdigest(), "")
                        return "".join(_misread_digit(ch, r) if ch.isdigit() and r.random() < prof.get("digit_noise", 0) else ch
                                       for ch in text)
        return ""


def _material_of(designation: str) -> str | None:
    """What a model that knows steel grades reads a designation as (coating suffixes ignored)."""
    key = _gnorm(re.sub(r"\+?(Z|ZM|AZ)\d{2,3}$", "", designation.strip()))
    for m in MATERIALS:
        names = (m.id, m.number, *m.legacy, *m.suffix, *m.kb_unknown)
        if any(_gnorm(n) == key for n in names):
            return m.id
    return None


def _gnorm(s: str) -> str:
    return re.sub(r"[\s\-+./]", "", s).upper()


def _misread_digit(ch: str, r) -> str:
    return {"1": "7", "7": "1", "5": "6", "6": "5", "0": "8", "8": "0", "3": "8"}.get(ch, ch)


def noisy_claims(truth: dict, prof: dict, r) -> dict:
    c = copy.deepcopy(truth)
    if not c.get("is_rfq"):
        return c
    for line in c["lines"]:
        for f, q in (("thickness_mm", "thickness_quote"), ("width_mm", "width_quote"), ("length_mm", "length_quote"),
                     ("quantity_t", "quantity_quote"), ("max_price_eur_t", "price_quote")):
            if line.get(f) is None:
                continue
            x = r.random()
            if x < prof["miss"]:
                line[f], line[q] = None, ""
            elif x < prof["miss"] + prof["misread"]:
                v = line[f]
                line[f] = r.choice([v * 10, v / 10, v * 1000 if f == "quantity_t" else v + 0.5, round(v * 1.1, 2)])
            elif x < prof["miss"] + prof["misread"] + prof["paraphrase_quote"]:
                line[q] = _paraphrase(f, line[f])
        if r.random() < prof["swap_dims"] and line.get("thickness_mm") is not None and line.get("width_mm") is not None:
            line["thickness_mm"], line["width_mm"] = line["width_mm"], line["thickness_mm"]
        if r.random() < prof["misread"]:
            line["grade"] = _typo(line["grade"], r)
        if r.random() < prof["miss"]:
            line["form"], line["form_quote"] = None, ""
        if r.random() < prof["hallucinate"] and line.get("max_price_eur_t") is None:
            line["max_price_eur_t"], line["price_quote"] = 700.0, "Zielpreis 700 €/t"
        if line.get("alternatives") and r.random() < prof["miss"] * 3:
            line["alternatives"] = []
    pol = c["policy"]
    if r.random() < prof["hallucinate"] and pol.get("certificate", "none") == "none":
        pol["certificate"], pol["certificate_quote"] = "3.1", "Abnahmeprüfzeugnis 3.1"
    if not pol.get("upgrades_ok") and r.random() < prof["policy_overread"]:
        pol["upgrades_ok"], pol["upgrades_quote"] = True, "Gleichwertige Güte möglich."
    if pol.get("excluded_origins") and r.random() < prof["miss"] * 2:
        pol["excluded_origins"], pol["origins_quote"] = [], ""
    if r.random() < prof["hallucinate"] / 2:
        c["delivery_quote"] = "Lieferung bis Ende des Monats"
    return c


def _paraphrase(field: str, v: float) -> str:
    return {"thickness_mm": f"{v:g}mm thick", "width_mm": f"width {v:g}mm", "length_mm": f"length {v:g}",
            "quantity_t": f"{v:g} tonnes total", "max_price_eur_t": f"max {v:g} EUR"}[field]


def _typo(s: str, r) -> str:
    if len(s) < 3:
        return s
    i = r.randrange(1, len(s) - 1)
    return s[:i] + s[i + 1] + s[i] + s[i + 2:]
