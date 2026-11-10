"""Tool registry: registration, argument validation, stage gating, timeout and retry (contract §2)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import jsonschema

from backend.common.schemas import ToolContext, ToolResult, ToolSpec

ToolHandler = Callable[[dict, ToolContext], ToolResult]


@dataclass
class Execution:
    result: ToolResult
    attempts: int


def validate_call(spec: ToolSpec, args: dict, stage: str,
                  enabled: list[str] | None = None) -> ToolResult | None:
    """Shared call checks for business tools and runtime control tools."""
    if stage not in spec.stages or (enabled is not None and spec.name not in enabled):
        return ToolResult.fail("NOT_ALLOWED", f"{spec.name} is not available in stage {stage}")
    try:
        jsonschema.validate(args, spec.parameters)
    except jsonschema.ValidationError as exc:
        return ToolResult.fail("INVALID_ARGS", exc.message)
    return None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolSpec, ToolHandler]] = {}
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tool")

    def register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        if spec.name in self._tools:
            raise ValueError(f"tool already registered: {spec.name}")
        jsonschema.Draft202012Validator.check_schema(spec.parameters)
        self._tools[spec.name] = (spec, handler)

    def unregister(self, name: str) -> None:
        """Remove a tool, e.g. to swap a mock for the real implementation."""
        del self._tools[name]

    def tool(self, spec: ToolSpec) -> Callable[[ToolHandler], ToolHandler]:
        """Decorator form of register()."""
        def wrap(handler: ToolHandler) -> ToolHandler:
            self.register(spec, handler)
            return handler
        return wrap

    def get_spec(self, name: str) -> ToolSpec | None:
        entry = self._tools.get(name)
        return entry[0] if entry else None

    def names(self) -> list[str]:
        return list(self._tools)

    def all_specs(self) -> list[ToolSpec]:
        return [spec for spec, _ in self._tools.values()]

    def available(self, stage: str, enabled: list[str] | None = None) -> list[ToolSpec]:
        """Tools the model may see in this stage, optionally narrowed by RunConfig.enabled_tools."""
        return [
            spec for spec, _ in self._tools.values()
            if stage in spec.stages and (enabled is None or spec.name in enabled)
        ]

    def execute(self, name: str, args: dict, ctx: ToolContext, enabled: list[str] | None = None) -> Execution:
        entry = self._tools.get(name)
        if entry is None:
            return Execution(ToolResult.fail("NOT_FOUND", f"unknown tool: {name}"), 0)
        spec, handler = entry
        error = validate_call(spec, args, ctx.stage, enabled)
        if error is not None:
            return Execution(error, 0)

        attempts = 0
        while True:
            remaining = (ctx.deadline - datetime.now(timezone.utc)).total_seconds()
            if remaining <= 0:
                return Execution(ToolResult.fail("TIMEOUT", f"{name} exceeded its execution deadline"), attempts)
            attempts += 1
            result = self._call_once(spec, handler, args, ctx, min(spec.timeout_s, remaining))
            retry = result.status == "error" and result.error is not None and result.error.retryable
            if spec.side_effects or not retry or attempts > spec.max_retries:
                return Execution(result, attempts)

    def _call_once(self, spec: ToolSpec, handler: ToolHandler, args: dict, ctx: ToolContext, timeout_s: float) -> ToolResult:
        future = self._pool.submit(handler, args, ctx)
        try:
            result = future.result(timeout=timeout_s)
        except FutureTimeout:
            # Cancel queued work; running threads cannot be killed.
            future.cancel()
            return ToolResult.fail("TIMEOUT", f"{spec.name} timed out after {timeout_s:.3f}s", retryable=not spec.side_effects)
        except Exception as e:  # tools should return errors, but never let one crash the run
            return ToolResult.fail("INTERNAL", f"{type(e).__name__}: {e}")
        if not isinstance(result, ToolResult):
            return ToolResult.fail("INTERNAL", f"{spec.name} returned {type(result).__name__}, expected ToolResult")
        return result

    def close(self, *, wait: bool = True) -> None:
        """Release workers; running handlers still need to finish cooperatively."""
        self._pool.shutdown(wait=wait, cancel_futures=True)

    def __enter__(self) -> "ToolRegistry":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
