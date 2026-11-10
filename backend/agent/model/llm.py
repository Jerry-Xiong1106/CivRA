"""LangChain model construction, message conversion and offline scripted model."""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from backend.common.schemas import CostInfo, LLMResponse, TokenUsage, ToolCall


def make_chat_model(*, model, base_url, api_key, timeout_s=120, price_in=None,
                    price_out=None, price_version=None) -> BaseChatModel:
    from langchain_openai import ChatOpenAI
    pricing = {"input": price_in, "output": price_out, "version": price_version}
    return ChatOpenAI(model=model, base_url=base_url, api_key=api_key, temperature=0,
                      timeout=timeout_s, max_retries=0, cache=False, use_responses_api=False,
                      metadata={"civra_pricing": pricing})


def _parse_args(raw):
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {"__unparsed__": raw}
    return parsed if isinstance(parsed, dict) else {"__unparsed__": raw}


def _text(content):
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)


def normalize_reply(reply: AIMessage) -> AIMessage:
    """Keep malformed tool calls visible to the registry, in provider order."""
    raw = reply.additional_kwargs.get("tool_calls")
    if raw:
        calls = [{"id": c.get("id") or f"call_{i}", "name": c["function"]["name"],
                  "args": _parse_args(c["function"].get("arguments")), "type": "tool_call"}
                 for i, c in enumerate(raw)]
        reply = reply.model_copy(update={"tool_calls": calls, "invalid_tool_calls": []})
    elif reply.invalid_tool_calls:
        calls = list(reply.tool_calls) + [
            {"id": c.get("id") or f"invalid_{i}", "name": c.get("name") or "",
             "args": _parse_args(c.get("args")), "type": "tool_call"}
            for i, c in enumerate(reply.invalid_tool_calls)]
        reply = reply.model_copy(update={"tool_calls": calls, "invalid_tool_calls": []})
    # Ordered execution needs unique IDs, even for malformed provider output.
    used, calls = set(), []
    for i, call in enumerate(reply.tool_calls):
        ident = call.get("id") or f"call_{i}"
        while ident in used:
            ident += f"_{i}"
        used.add(ident)
        calls.append({**call, "id": ident})
    return reply.model_copy(update={"tool_calls": calls})


def message_dict(message: BaseMessage) -> dict:
    role = {"ai": "assistant", "human": "user", "system": "system", "tool": "tool"}[message.type]
    result = {"role": role, "content": _text(message.content)}
    if isinstance(message, AIMessage) and message.tool_calls:
        result["tool_calls"] = [
            {"id": c["id"], "type": "function",
             "function": {"name": c["name"], "arguments": json.dumps(c["args"], ensure_ascii=False)}}
            for c in message.tool_calls]
    if isinstance(message, ToolMessage):
        result["tool_call_id"] = message.tool_call_id
    return result


def response_from_message(reply: AIMessage, model: BaseChatModel, latency_ms=0) -> LLMResponse:
    if reply.invalid_tool_calls:
        reply = normalize_reply(reply)
    if "civra_usage" in reply.additional_kwargs:  # scripted tests can represent partial usage
        usage = TokenUsage(**reply.additional_kwargs["civra_usage"])
    elif reply.usage_metadata:
        meta = reply.usage_metadata
        usage = TokenUsage(prompt_tokens=meta.get("input_tokens"), completion_tokens=meta.get("output_tokens"),
                           total_tokens=meta.get("total_tokens"),
                           cached_tokens=(meta.get("input_token_details") or {}).get("cache_read"))
    else:
        usage = TokenUsage()
    raw_cost = reply.additional_kwargs.get("civra_cost")
    cost = CostInfo(**raw_cost) if raw_cost else None
    pricing = (model.metadata or {}).get("civra_pricing", {})
    if cost is None and pricing.get("input") and pricing.get("output") and (
            usage.prompt_tokens is not None and usage.completion_tokens is not None):
        amount = (Decimal(pricing["input"]) * usage.prompt_tokens +
                  Decimal(pricing["output"]) * usage.completion_tokens) / Decimal(1_000_000)
        cost = CostInfo(amount=str(amount.quantize(Decimal("0.000001"))), price_version=pricing.get("version"))
    info = reply.response_metadata or {}
    return LLMResponse(
        content=_text(reply.content) or None,
        tool_calls=[ToolCall(id=c["id"], tool_name=c["name"], arguments=c["args"]) for c in reply.tool_calls],
        finish_reason=info.get("finish_reason"), usage=usage, cost=cost,
        model=info.get("model_name") or getattr(model, "model_name", ""),
        provider_request_id=info.get("id"), latency_ms=latency_ms,
    )


class ScriptedLLM(BaseChatModel):
    """Offline BaseChatModel; uses the same create_agent path as production."""
    script: list[Any]
    calls: list[dict] = Field(default_factory=list)
    model_name: str = "scripted"

    def __init__(self, script, **kwargs):
        super().__init__(script=list(script), cache=False, **kwargs)

    @property
    def _llm_type(self):
        return "civra-scripted"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self.bind(tools=tools, tool_choice=tool_choice, **kwargs)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        tools = kwargs.get("tools") or []
        history = [message_dict(m) for m in messages]
        self.calls.append({"messages": history, "tools": [t.name for t in tools],
                           "model": kwargs.get("model"), "temperature": kwargs.get("temperature", 0.0),
                           "max_tokens": kwargs.get("max_tokens"), "seed": kwargs.get("seed"),
                           "tool_choice": kwargs.get("tool_choice")})
        if not self.script:
            raise RuntimeError("ScriptedLLM script exhausted")
        step = self.script.pop(0)
        response = step(history, tools) if callable(step) else step
        usage = response.usage
        if usage.prompt_tokens is None:
            usage = TokenUsage(prompt_tokens=100, completion_tokens=20, total_tokens=120)
        info = {"finish_reason": response.finish_reason or ("tool_calls" if response.tool_calls else "stop"),
                "model_name": response.model or kwargs.get("model") or self.model_name,
                "id": response.provider_request_id}
        message = AIMessage(
            content=response.content or "",
            tool_calls=[{"id": c.id, "name": c.tool_name, "args": c.arguments, "type": "tool_call"}
                        for c in response.tool_calls],
            response_metadata=info,
            additional_kwargs={"civra_usage": usage.model_dump(),
                               "civra_cost": response.cost.model_dump() if response.cost else None},
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


_call_counter = 0


def tool_call(name: str, **arguments: Any) -> LLMResponse:
    """Build a scripted model response, not an alternate execution loop."""
    global _call_counter
    _call_counter += 1
    return LLMResponse(tool_calls=[ToolCall(id=f"call_{_call_counter}", tool_name=name, arguments=arguments)])


def latest_answer(messages: list[BaseMessage]) -> str:
    return next((message.text for message in reversed(messages)
                 if isinstance(message, AIMessage) and message.text), "")
