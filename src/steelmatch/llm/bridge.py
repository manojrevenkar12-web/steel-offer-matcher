"""A Pydantic AI model that talks to the LiteLLM gateway in-process.

Pydantic AI owns the agent loop (tools, output validation, retries on invalid output);
LiteLLM owns the call (routing, fallbacks, retries on provider errors, cost). This adapter
turns Pydantic AI messages into OpenAI-format messages for LiteLLM and turns the reply back.
With a LiteLLM proxy instead of the in-process router, the same agents run on Pydantic AI's
OpenAI model pointed at the proxy URL; nothing else changes.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from .gateway import Gateway


def to_openai(messages: list[ModelMessage], instructions: str | None) -> list[dict]:
    out: list[dict] = []
    if instructions:
        out.append({"role": "system", "content": instructions})
    for m in messages:
        if isinstance(m, ModelRequest):
            for p in m.parts:
                if isinstance(p, SystemPromptPart):
                    out.append({"role": "system", "content": p.content})
                elif isinstance(p, UserPromptPart):
                    out.append({"role": "user", "content": _user_content(p.content)})
                elif isinstance(p, ToolReturnPart):
                    content = p.content if isinstance(p.content, str) else json.dumps(p.content, default=str)
                    out.append({"role": "tool", "tool_call_id": p.tool_call_id, "content": content})
                elif isinstance(p, RetryPromptPart):
                    if p.tool_call_id and p.tool_name:
                        out.append({"role": "tool", "tool_call_id": p.tool_call_id, "content": p.model_response()})
                    else:
                        out.append({"role": "user", "content": p.model_response()})
        elif isinstance(m, ModelResponse):
            calls = [p for p in m.parts if isinstance(p, ToolCallPart)]
            text = "".join(p.content for p in m.parts if isinstance(p, TextPart))
            msg: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = [{"id": c.tool_call_id, "type": "function",
                                      "function": {"name": c.tool_name, "arguments": c.args_as_json_str()}} for c in calls]
            out.append(msg)
    return out


def _user_content(content: Any) -> Any:
    if isinstance(content, str):
        return content
    parts: list[dict] = []
    for c in content:
        if isinstance(c, str):
            parts.append({"type": "text", "text": c})
        elif isinstance(c, BinaryContent):
            b64 = base64.b64encode(c.data).decode()
            parts.append({"type": "image_url", "image_url": {"url": f"data:{c.media_type};base64,{b64}"}})
    return parts


def tools_of(info: AgentInfo) -> list[dict]:
    return [{"type": "function", "function": {"name": t.name, "description": t.description or "",
                                              "parameters": t.parameters_json_schema}}
            for t in [*info.function_tools, *info.output_tools]]


def gateway_model(gateway: Gateway, route: str, agent_name: str, metadata: dict | None = None) -> FunctionModel:
    async def call(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        resp = await gateway.complete(route, to_openai(messages, info.instructions), tools_of(info),
                                      {"agent": agent_name, **(metadata or {})})
        choice = resp.choices[0].message
        parts: list = []
        if getattr(choice, "content", None):
            parts.append(TextPart(choice.content))
        for tc in getattr(choice, "tool_calls", None) or []:
            parts.append(ToolCallPart(tc.function.name, tc.function.arguments, tool_call_id=tc.id))
        usage = getattr(resp, "usage", None)
        return ModelResponse(parts=parts, usage=RequestUsage(
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0)), model_name=route)

    return FunctionModel(call, model_name=f"gateway:{route}")
