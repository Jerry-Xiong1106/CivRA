"""Tool registry: registration, argument validation, stage gating, timeout and retry (contract §2)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Callable

import jsonschema

from backend.common.schemas import ToolContext, ToolResult, ToolSpec

ToolHandler = Callable[[dict, ToolContext], ToolResult]


@dataclass
class Execution:
    result: ToolResult
    attempts: int


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
        if ctx.stage not in spec.stages or (enabled is not None and name not in enabled):
            return Execution(ToolResult.fail("NOT_ALLOWED", f"{name} is not available in stage {ctx.stage}"), 0)
        try:
            jsonschema.validate(args, spec.parameters)
        except jsonschema.ValidationError as e:
            return Execution(ToolResult.fail("INVALID_ARGS", e.message), 0)

        attempts = 0
        while True:
            attempts += 1
            result = self._call_once(spec, handler, args, ctx)
            retry = result.status == "error" and result.error is not None and result.error.retryable
            if not retry or attempts > spec.max_retries:
                return Execution(result, attempts)

    def _call_once(self, spec: ToolSpec, handler: ToolHandler, args: dict, ctx: ToolContext) -> ToolResult:
        future = self._pool.submit(handler, args, ctx)
        try:
            result = future.result(timeout=spec.timeout_s)
        except FutureTimeout:
            # The worker thread cannot be killed; its late result is discarded.
            return ToolResult.fail("TIMEOUT", f"{spec.name} timed out after {spec.timeout_s}s", retryable=True)
        except Exception as e:  # tools should return errors, but never let one crash the run
            return ToolResult.fail("INTERNAL", f"{type(e).__name__}: {e}")
        if not isinstance(result, ToolResult):
            return ToolResult.fail("INTERNAL", f"{spec.name} returned {type(result).__name__}, expected ToolResult")
        return result


default_registry = ToolRegistry()


def register_tool(spec: ToolSpec) -> Callable[[ToolHandler], ToolHandler]:
    """Register a tool on the shared default registry."""
    return default_registry.tool(spec)
