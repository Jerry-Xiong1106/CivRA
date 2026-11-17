"""Execution limits used by middleware around native ToolNode calls."""
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import jsonschema
from langchain_core.tools import BaseTool
from pydantic import ValidationError

from backend.common.schemas import ToolContext, ToolResult
from backend.tools.policy import parameters_of, policy_of


def validate_call(tool: BaseTool, args: dict, stage: str,
                  enabled: list[str] | None = None) -> ToolResult | None:
    if stage not in policy_of(tool).stages or (enabled is not None and tool.name not in enabled):
        return ToolResult.fail("NOT_ALLOWED", f"{tool.name} is not available in stage {stage}")
    if "runtime" in args:
        return ToolResult.fail("INVALID_ARGS", "runtime is supplied by the application")
    try:
        jsonschema.validate(args, parameters_of(tool))
    except jsonschema.ValidationError as exc:
        return ToolResult.fail("INVALID_ARGS", exc.message)
    return None


@dataclass
class Execution:
    result: ToolResult
    attempts: int


class ToolExecutor:
    """Own workers for one drive/resume, never a tool registry or dispatcher."""
    def __init__(self):
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent-tool")

    def execute(self, tool: BaseTool | None, name: str, args: dict, ctx: ToolContext,
                call: Callable[[], ToolResult], enabled: list[str] | None = None) -> Execution:
        if tool is None:
            return Execution(ToolResult.fail("NOT_FOUND", f"unknown tool: {name}"), 0)
        error = validate_call(tool, args, ctx.stage, enabled)
        if error is not None:
            return Execution(error, 0)
        policy = policy_of(tool)
        attempts = 0
        while True:
            remaining = (ctx.deadline - datetime.now(timezone.utc)).total_seconds()
            if remaining <= 0:
                return Execution(ToolResult.fail("TIMEOUT", f"{name} exceeded its execution deadline"), attempts)
            attempts += 1
            timeout = min(policy.timeout_s, remaining)
            future = self._pool.submit(call)
            try:
                result = future.result(timeout=timeout)
                if not isinstance(result, ToolResult):
                    result = ToolResult.fail("INTERNAL", f"{name} returned an invalid tool result")
            except FutureTimeout:
                # Running Python threads cannot be forcibly stopped.
                future.cancel()
                result = ToolResult.fail("TIMEOUT", f"{name} timed out after {timeout:.3f}s",
                                         retryable=not policy.side_effects)
            except ValidationError:
                result = ToolResult.fail("INVALID_ARGS", "Tool arguments or result failed validation")
            except Exception as exc:
                result = ToolResult.fail("INTERNAL", f"Tool execution failed ({type(exc).__name__})")
            retry = result.status == "error" and result.error is not None and result.error.retryable
            if policy.side_effects or not retry or attempts > policy.max_retries:
                return Execution(result, attempts)

    def close(self, *, wait=False):
        self._pool.shutdown(wait=wait, cancel_futures=True)
