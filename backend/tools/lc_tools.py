"""Registered tools as LangChain StructuredTools.

Lets LangChain chains, or a Fixed Workflow baseline, call CivRA tools through
the same registry path as the Agent: argument validation, stage gating,
timeout and retry all still apply, and the result is the ToolResult as a dict.

    tools = to_langchain_tools(registry, cases, case_id="eval-case-1")
    result = next(t for t in tools if t.name == "search_statutes").invoke({"query": "交强险"})
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from langchain_core.tools import StructuredTool

from backend.agent.case_port import CaseStatePort
from backend.common.schemas import ToolContext, ToolSpec
from backend.tools.registry import ToolRegistry


def to_langchain_tools(
    registry: ToolRegistry,
    cases: CaseStatePort,
    case_id: str,
    *,
    run_id: str = "langchain",
    names: list[str] | None = None,
) -> list[StructuredTool]:
    """One StructuredTool per registered tool available in the case's current stage."""
    stage = cases.get_snapshot(case_id).stage
    specs = [s for s in registry.available(stage) if names is None or s.name in names]
    return [_wrap(registry, cases, case_id, run_id, spec) for spec in specs]


def _wrap(registry: ToolRegistry, cases: CaseStatePort, case_id: str, run_id: str, spec: ToolSpec) -> StructuredTool:
    calls = {"n": 0}

    def run(**arguments: Any) -> dict:
        calls["n"] += 1
        snap = cases.get_snapshot(case_id)  # read per call: stage and version may have changed
        ctx = ToolContext(case_id=case_id, run_id=run_id, step_id=calls["n"], case_version=snap.case_version,
                          deadline=datetime.now(timezone.utc) + timedelta(seconds=spec.timeout_s), stage=snap.stage)
        execution = registry.execute(spec.name, arguments, ctx)
        return {**execution.result.model_dump(mode="json", exclude_none=True), "attempts": execution.attempts}

    return StructuredTool.from_function(func=run, name=spec.name, description=spec.description,
                                        args_schema=spec.parameters)
