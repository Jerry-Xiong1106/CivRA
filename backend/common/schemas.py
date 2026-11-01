"""Cross-module data structures for the agent runtime.

Mirrors docs/agent-runtime-contract.md. Change the doc and this file together.
SourceRef is owned by member 2; only its minimum fields are defined here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


# ---------------------------------------------------------------- tools (§2)

Stage = Literal["consultation", "route_selection", "negotiation", "mediation", "litigation", "closed"]
ALL_STAGES: list[str] = ["consultation", "route_selection", "negotiation", "mediation", "litigation", "closed"]

ToolStatus = Literal["ok", "empty", "insufficient", "error"]
ErrorCode = Literal["INVALID_ARGS", "NOT_ALLOWED", "NOT_FOUND", "TIMEOUT", "UPSTREAM", "INTERNAL"]


class ToolError(BaseModel):
    code: ErrorCode
    message: str
    retryable: bool = False


class SourceRef(BaseModel):
    source_id: str
    source_type: Literal[
        "statute", "judicial_interpretation", "case", "template", "guideline", "user_material"
    ]
    title: str
    locator: str
    version: str | None = None
    excerpt: str | None = None
    url: str | None = None


class ToolResult(BaseModel):
    status: ToolStatus
    data: dict[str, Any] | None = None
    summary: str = ""
    source_refs: list[SourceRef] = Field(default_factory=list)
    error: ToolError | None = None
    case_version: int | None = None

    @classmethod
    def fail(cls, code: ErrorCode, message: str, retryable: bool = False) -> "ToolResult":
        return cls(status="error", summary=message, error=ToolError(code=code, message=message, retryable=retryable))


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]
    owner: str
    stages: list[str] = Field(default_factory=lambda: list(ALL_STAGES))
    timeout_s: float = 30.0
    max_retries: int = 1
    side_effects: bool = False


class ToolContext(BaseModel):
    case_id: str
    run_id: str
    step_id: int
    case_version: int
    deadline: datetime
    stage: str


class ToolCall(BaseModel):
    id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------- model (§3)

class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0


class LLMResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    model: str = ""
    latency_ms: int = 0
    estimated_cost: str = "0"


# ---------------------------------------------------------------- runs (§4, §6)

RunStatus = Literal["running", "waiting_user", "completed", "failed", "cancelled", "budget_exhausted"]
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "budget_exhausted"}


class RunConfig(BaseModel):
    mode: Literal["direct_llm", "rag", "fixed_workflow", "agent"] = "agent"
    model: str | None = None
    temperature: float = 0.0
    max_steps: int = 30
    max_tool_calls: int = 20
    enabled_tools: list[str] | None = None
    seed: int | None = None


class UserReply(BaseModel):
    text: str | None = None
    answers: dict[str, str] = Field(default_factory=dict)
    route_choice: str | None = None
    file_ids: list[str] = Field(default_factory=list)


class RunUsage(BaseModel):
    steps: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost: str = "0"
    duration_ms: int = 0


class FinalOutput(BaseModel):
    answer: str
    output_refs: list[str] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)
    open_issues: list[str] = Field(default_factory=list)
    case_version: int


class RunInfo(BaseModel):
    run_id: str
    case_id: str
    status: RunStatus
    pending: dict[str, Any] | None = None
    final: FinalOutput | None = None
    usage: RunUsage = Field(default_factory=RunUsage)
    error: str | None = None


# ---------------------------------------------------------------- events and trace (§5)

EventType = Literal[
    "run.started", "run.resumed", "step.started", "step.finished",
    "assistant.delta", "run.waiting_user",
    "run.completed", "run.failed", "run.cancelled", "run.budget_exhausted",
]


class RunEvent(BaseModel):
    seq: int
    run_id: str
    type: EventType
    step_id: int | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    at: str = Field(default_factory=now_iso)


StepType = Literal["model_call", "tool_call", "ask_user", "route_choice", "verify", "finish", "error"]
StepStatus = Literal["ok", "empty", "insufficient", "error", "retried", "waiting", "cancelled"]


class TraceStep(BaseModel):
    case_id: str
    run_id: str
    step_id: int
    step_type: StepType
    tool_name: str | None = None
    arguments: dict[str, Any] | None = None
    input_ref: str | None = None
    result_summary: str = ""
    result_ref: str | None = None
    source_refs: list[SourceRef] = Field(default_factory=list)
    status: StepStatus
    error: ToolError | None = None
    attempt: int = 1
    started_at: str
    duration_ms: int = 0
    token_usage: TokenUsage | None = None
    estimated_cost: str | None = None
    case_version: int
    output_version: int | None = None
    decision_note: str | None = None
