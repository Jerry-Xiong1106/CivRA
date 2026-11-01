"""Unified model interface (contract §3).

Messages use the OpenAI-compatible chat format (role / content / tool_calls /
tool_call_id), so DeepSeek and other compatible providers work unchanged.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from decimal import Decimal
from typing import Any, Callable, Protocol, Sequence

from backend.common.schemas import LLMResponse, TokenUsage, ToolCall, ToolSpec


class LLMClient(Protocol):
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Sequence[ToolSpec] | None = None,
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> LLMResponse: ...


def tools_to_openai(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
        for t in tools
    ]


class OpenAICompatibleClient:
    """Calls any /chat/completions endpoint with tool calling.

    Config comes from the environment (see .env.example):
    CIVRA_LLM_BASE_URL, CIVRA_LLM_API_KEY, CIVRA_LLM_MODEL,
    CIVRA_LLM_PRICE_IN / CIVRA_LLM_PRICE_OUT (yuan per million tokens).
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        price_in: str | None = None,
        price_out: str | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        self.base_url = (base_url or os.environ["CIVRA_LLM_BASE_URL"]).rstrip("/")
        self.api_key = api_key or os.environ["CIVRA_LLM_API_KEY"]
        self.model = model or os.environ["CIVRA_LLM_MODEL"]
        self.price_in = Decimal(price_in or os.environ.get("CIVRA_LLM_PRICE_IN", "0"))
        self.price_out = Decimal(price_out or os.environ.get("CIVRA_LLM_PRICE_OUT", "0"))
        self.timeout_s = timeout_s

    def chat(self, messages, tools=None, *, temperature=0.0, max_tokens=None) -> LLMResponse:
        body: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": temperature}
        if tools:
            body["tools"] = tools_to_openai(tools)
        if max_tokens:
            body["max_tokens"] = max_tokens
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
        )
        start = time.monotonic()
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            payload = json.load(resp)
        latency_ms = int((time.monotonic() - start) * 1000)

        msg = payload["choices"][0]["message"]
        calls = [
            ToolCall(id=c["id"], tool_name=c["function"]["name"], arguments=_parse_args(c["function"]["arguments"]))
            for c in msg.get("tool_calls") or []
        ]
        u = payload.get("usage") or {}
        usage = TokenUsage(
            prompt_tokens=u.get("prompt_tokens", 0),
            completion_tokens=u.get("completion_tokens", 0),
            cached_tokens=(u.get("prompt_tokens_details") or {}).get("cached_tokens", 0),
        )
        cost = (self.price_in * usage.prompt_tokens + self.price_out * usage.completion_tokens) / Decimal(1_000_000)
        return LLMResponse(
            content=msg.get("content"),
            tool_calls=calls,
            usage=usage,
            model=payload.get("model", self.model),
            latency_ms=latency_ms,
            estimated_cost=str(cost.quantize(Decimal("0.000001"))),
        )


def _parse_args(raw: str | dict) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        # Keep the raw text so the registry's schema check reports INVALID_ARGS to the model.
        return {"__unparsed__": raw}
    return parsed if isinstance(parsed, dict) else {"__unparsed__": raw}


ScriptStep = LLMResponse | Callable[[list[dict[str, Any]], Sequence[ToolSpec]], LLMResponse]


class ScriptedLLM:
    """Deterministic stand-in for tests and demos: returns the scripted responses in order.

    A step may be a callable that inspects the messages and offered tools, so a
    script can assert on what the runtime sent to the model.
    """

    def __init__(self, script: list[ScriptStep]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages, tools=None, *, temperature=0.0, max_tokens=None) -> LLMResponse:
        self.calls.append({"messages": [dict(m) for m in messages], "tools": [t.name for t in tools or []]})
        if not self.script:
            raise RuntimeError("ScriptedLLM script exhausted")
        step = self.script.pop(0)
        resp = step(messages, tools or []) if callable(step) else step
        if resp.usage.prompt_tokens == 0:
            resp = resp.model_copy(update={"usage": TokenUsage(prompt_tokens=100, completion_tokens=20)})
        return resp.model_copy(update={"model": resp.model or "scripted"})


_call_counter = 0


def tool_call(name: str, **arguments: Any) -> LLMResponse:
    """Shorthand for a scripted response that calls one tool."""
    global _call_counter
    _call_counter += 1
    return LLMResponse(tool_calls=[ToolCall(id=f"call_{_call_counter}", tool_name=name, arguments=arguments)])
