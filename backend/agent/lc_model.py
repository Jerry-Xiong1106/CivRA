"""LLMClient backed by a LangChain chat model (contract §3).

The runner keeps its own loop; LangChain handles the provider call. Messages
stay in the OpenAI-compatible format inside the runtime and are converted at
this boundary, so traces, checkpoints and evaluation exports do not change.

    client = LangChainLLMClient(ChatOpenAI(model="deepseek-chat", base_url=..., api_key=..., max_retries=0))
"""

from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Any, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from backend.agent.llm import ToolChoice, _parse_args, tools_to_openai
from backend.common.schemas import CostInfo, LLMResponse, TokenUsage, ToolCall, ToolSpec


class LangChainLLMClient:
    """Adapts any LangChain BaseChatModel with tool calling to the LLMClient protocol.

    Per-call model, temperature, max_tokens and seed are applied to a copy of
    the chat model, so one client serves every RunConfig. Build the chat model
    with max_retries=0 so each chat() is one provider attempt (attempt_count=1).
    """

    def __init__(
        self,
        chat_model: BaseChatModel,
        *,
        price_in: str | None = None,
        price_out: str | None = None,
        price_version: str | None = None,
    ) -> None:
        self.chat_model = chat_model
        self.model = getattr(chat_model, "model_name", None) or getattr(chat_model, "model", "") or type(chat_model).__name__
        self.prices = (Decimal(price_in), Decimal(price_out)) if price_in and price_out else None
        self.price_version = price_version

    def chat(self, messages, tools=None, *, model=None, temperature=0.0, max_tokens=None,
             seed=None, tool_choice=None) -> LLMResponse:
        runnable = self._configured(model=model, temperature=temperature, max_tokens=max_tokens, seed=seed)
        if tools:
            runnable = runnable.bind_tools(tools_to_openai(tools), tool_choice=tool_choice)
        start = time.monotonic()
        reply = runnable.invoke(to_langchain_messages(messages))
        latency_ms = int((time.monotonic() - start) * 1000)
        return self._to_response(reply, model or self.model, latency_ms)

    def _configured(self, **params: Any) -> BaseChatModel:
        # Field names differ between chat model classes; only set the ones this model has.
        names = {"model": "model_name", "temperature": "temperature", "max_tokens": "max_tokens", "seed": "seed"}
        fields = type(self.chat_model).model_fields
        update = {names[k]: v for k, v in params.items()
                  if v is not None and names[k] in fields and not (k == "model" and v == self.model)}
        return self.chat_model.model_copy(update=update) if update else self.chat_model

    def _to_response(self, reply: AIMessage, requested_model: str, latency_ms: int) -> LLMResponse:
        calls = [ToolCall(id=c["id"] or f"call_{i}", tool_name=c["name"], arguments=c["args"] or {})
                 for i, c in enumerate(reply.tool_calls)]
        # Malformed JSON arguments: keep the raw text so the registry reports INVALID_ARGS to the model.
        calls += [ToolCall(id=c.get("id") or f"invalid_{i}", tool_name=c.get("name") or "",
                           arguments=_parse_args(c.get("args") or ""))
                  for i, c in enumerate(reply.invalid_tool_calls)]

        meta = reply.usage_metadata
        usage = TokenUsage()
        if meta:
            usage = TokenUsage(
                prompt_tokens=meta.get("input_tokens"),
                completion_tokens=meta.get("output_tokens"),
                total_tokens=meta.get("total_tokens"),
                cached_tokens=(meta.get("input_token_details") or {}).get("cache_read"),
            )
        cost = None
        if self.prices and usage.prompt_tokens is not None and usage.completion_tokens is not None:
            amount = (self.prices[0] * usage.prompt_tokens + self.prices[1] * usage.completion_tokens) / Decimal(1_000_000)
            cost = CostInfo(amount=str(amount.quantize(Decimal("0.000001"))), price_version=self.price_version)

        info = reply.response_metadata or {}
        return LLMResponse(
            content=_text(reply.content) or None,
            tool_calls=calls,
            finish_reason=info.get("finish_reason"),
            usage=usage,
            cost=cost,
            model=info.get("model_name") or requested_model,
            provider_request_id=info.get("id") or reply.id,
            latency_ms=latency_ms,
        )


def to_langchain_messages(messages: Sequence[dict[str, Any]]) -> list[BaseMessage]:
    """OpenAI-format dicts (as stored in checkpoints) -> LangChain messages."""
    out: list[BaseMessage] = []
    for m in messages:
        role, content = m["role"], m.get("content") or ""
        if role == "system":
            out.append(SystemMessage(content=content))
        elif role == "user":
            out.append(HumanMessage(content=content))
        elif role == "assistant":
            calls = [{"id": c["id"], "name": c["function"]["name"],
                      "args": _parse_args(c["function"]["arguments"]), "type": "tool_call"}
                     for c in m.get("tool_calls") or []]
            out.append(AIMessage(content=content, tool_calls=calls))
        elif role == "tool":
            out.append(ToolMessage(content=content, tool_call_id=m["tool_call_id"]))
        else:
            raise ValueError(f"unsupported message role: {role}")
    return out


def _text(content: str | list) -> str:
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
