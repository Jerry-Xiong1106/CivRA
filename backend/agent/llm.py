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

from backend.common.schemas import CostInfo, LLMResponse, TokenUsage, ToolCall, ToolSpec

# "auto" | "none" | "required" | a tool name (force that tool). None = provider default.
ToolChoice = str | None


class LLMClient(Protocol):
    model: str   # default model identity, reported in the runtime manifest

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Sequence[ToolSpec] | None = None,
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        seed: int | None = None,
        tool_choice: ToolChoice = None,
    ) -> LLMResponse: ...


def tools_to_openai(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
        for t in tools
    ]


def tool_choice_to_openai(choice: ToolChoice) -> Any:
    if choice is None or choice in ("auto", "none", "required"):
        return choice
    return {"type": "function", "function": {"name": choice}}


class OpenAICompatibleClient:
    """Calls any /chat/completions endpoint with tool calling.

    Config comes from the environment (see .env.example):
    CIVRA_LLM_BASE_URL, CIVRA_LLM_API_KEY, CIVRA_LLM_MODEL,
    CIVRA_LLM_PRICE_IN / CIVRA_LLM_PRICE_OUT (yuan per million tokens),
    CIVRA_LLM_PRICE_VERSION (e.g. the date the prices were checked).

    No automatic retry: every call is one HTTP attempt (usage.attempt_count == 1),
    so callers can retry without double-counting.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        price_in: str | None = None,
        price_out: str | None = None,
        price_version: str | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        self.base_url = (base_url or os.environ["CIVRA_LLM_BASE_URL"]).rstrip("/")
        self.api_key = api_key or os.environ["CIVRA_LLM_API_KEY"]
        self.model = model or os.environ["CIVRA_LLM_MODEL"]
        price_in = price_in or os.environ.get("CIVRA_LLM_PRICE_IN")
        price_out = price_out or os.environ.get("CIVRA_LLM_PRICE_OUT")
        # Without both prices the cost is unknown (None), not zero.
        self.prices = (Decimal(price_in), Decimal(price_out)) if price_in and price_out else None
        self.price_version = price_version or os.environ.get("CIVRA_LLM_PRICE_VERSION")
        self.timeout_s = timeout_s

    def chat(self, messages, tools=None, *, model=None, temperature=0.0, max_tokens=None,
             seed=None, tool_choice=None) -> LLMResponse:
        body: dict[str, Any] = {"model": model or self.model, "messages": messages, "temperature": temperature}
        if tools:
            body["tools"] = tools_to_openai(tools)
            if tool_choice is not None:
                body["tool_choice"] = tool_choice_to_openai(tool_choice)
        if max_tokens:
            body["max_tokens"] = max_tokens
        if seed is not None:
            body["seed"] = seed  # not every provider honours it; recorded in the trace either way
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
        )
        start = time.monotonic()
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            payload = json.load(resp)
        latency_ms = int((time.monotonic() - start) * 1000)

        choice = payload["choices"][0]
        msg = choice["message"]
        calls = [
            ToolCall(id=c["id"], tool_name=c["function"]["name"], arguments=_parse_args(c["function"]["arguments"]))
            for c in msg.get("tool_calls") or []
        ]
        u = payload.get("usage") or {}
        usage = TokenUsage(
            prompt_tokens=u.get("prompt_tokens"),
            completion_tokens=u.get("completion_tokens"),
            total_tokens=u.get("total_tokens"),
            cached_tokens=(u.get("prompt_tokens_details") or {}).get("cached_tokens"),
        )
        cost = None
        if self.prices and usage.prompt_tokens is not None and usage.completion_tokens is not None:
            amount = (self.prices[0] * usage.prompt_tokens + self.prices[1] * usage.completion_tokens) / Decimal(1_000_000)
            cost = CostInfo(amount=str(amount.quantize(Decimal("0.000001"))), price_version=self.price_version)
        return LLMResponse(
            content=msg.get("content"),
            tool_calls=calls,
            finish_reason=choice.get("finish_reason"),
            usage=usage,
            cost=cost,
            model=payload.get("model", body["model"]),
            provider_request_id=payload.get("id"),
            latency_ms=latency_ms,
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

    model = "scripted"

    def __init__(self, script: list[ScriptStep]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages, tools=None, *, model=None, temperature=0.0, max_tokens=None,
             seed=None, tool_choice=None) -> LLMResponse:
        self.calls.append({
            "messages": [dict(m) for m in messages], "tools": [t.name for t in tools or []],
            "model": model, "temperature": temperature, "max_tokens": max_tokens,
            "seed": seed, "tool_choice": tool_choice,
        })
        if not self.script:
            raise RuntimeError("ScriptedLLM script exhausted")
        step = self.script.pop(0)
        resp = step(messages, tools or []) if callable(step) else step
        updates: dict[str, Any] = {"model": resp.model or model or self.model}
        if resp.usage.prompt_tokens is None:
            updates["usage"] = TokenUsage(prompt_tokens=100, completion_tokens=20, total_tokens=120)
        if resp.finish_reason is None:
            updates["finish_reason"] = "tool_calls" if resp.tool_calls else "stop"
        return resp.model_copy(update=updates)


_call_counter = 0


def tool_call(name: str, **arguments: Any) -> LLMResponse:
    """Shorthand for a scripted response that calls one tool."""
    global _call_counter
    _call_counter += 1
    return LLMResponse(tool_calls=[ToolCall(id=f"call_{_call_counter}", tool_name=name, arguments=arguments)])
