"""Cross-module data structures for the agent runtime.

Mirrors docs/agent-runtime-contract.md. Change the doc and this file together.
SourceRef is owned by member 2; only its minimum fields are defined here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


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
    details: dict[str, Any] | None = None


class SourceLocator(BaseModel):
    model_config = {"extra": "forbid"}

    article: str | None = None
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    paragraph: str | None = None
    chunk_id: str | None = None

    def __str__(self) -> str:
        location = self.article or self.paragraph or self.chunk_id or ""
        if self.page_start is not None:
            pages = str(self.page_start)
            if self.page_end is not None and self.page_end != self.page_start:
                pages += f"–{self.page_end}"
            location += f"（PDF第{pages}页）"
        elif self.page_end is not None:
            location += f"（PDF第{self.page_end}页）"
        return location

    @model_validator(mode="after")
    def check_location(self) -> "SourceLocator":
        if not any((self.article, self.page_start, self.page_end, self.paragraph, self.chunk_id)):
            raise ValueError("At least one source location is required")
        if self.page_start is not None and self.page_end is not None and self.page_start > self.page_end:
            raise ValueError("page_start must not exceed page_end")
        return self


class SourceRef(BaseModel):
    model_config = {"extra": "forbid"}

    source_id: str
    source_type: Literal[
        "statute", "judicial_interpretation", "case", "template", "guideline", "user_material"
    ]
    title: str
    locator: SourceLocator
    source_version: str
    effective_from: str | None = None
    effective_to: str | None = None
    decision_date: str | None = None
    retrieved_at: str | None = None
    retrieved_date: str | None = None
    source_file: str | None = None
    content_sha256: str | None = None
    url: str | None = None

class ToolResult(BaseModel):
    model_config = {"extra": "forbid"}

    status: ToolStatus
    data: dict[str, Any] | None = None
    summary: str = ""
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
    # Tools in the same feedback loop share a round limit, e.g. "retrieve_verify" for
    # search_statutes / search_cases / verify_output. Every non-ok result uses one round.
    feedback_loop: str | None = None


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
    """None means the provider did not report the count (never 0 for unknown)."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None   # included in prompt_tokens, not additional
    attempt_count: int = 1             # HTTP attempts for this call; the client does not retry today


class CostInfo(BaseModel):
    amount: str                        # decimal string
    currency: str = "CNY"
    basis: Literal["estimated", "billed"] = "estimated"
    price_version: str | None = None


class LLMResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str | None = None   # provider value, e.g. "stop", "tool_calls", "length"
    usage: TokenUsage = Field(default_factory=TokenUsage)
    cost: CostInfo | None = None       # None when no price is configured
    model: str = ""                    # model identity reported by the provider
    provider_request_id: str | None = None
    latency_ms: int = 0


# ---------------------------------------------------------------- runs (§4, §6)

RunStatus = Literal["running", "waiting_user", "completed", "failed", "cancelled", "budget_exhausted"]
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "budget_exhausted"}


class RunConfig(BaseModel):
    conversation_route: Literal["auto", "consultation", "case_work"] = "auto"
    max_search_calls: int | None = Field(default=3, ge=0)
    max_clarification_rounds: int | None = Field(default=2, ge=0)
    max_questions_per_round: int = Field(default=2, ge=1)
    mode: Literal["direct_llm", "rag", "fixed_workflow", "agent"] = "agent"
    model: str | None = None                 # None = the client's default model
    temperature: float = 0.0
    seed: int | None = None
    max_output_tokens: int | None = None     # per model call
    max_steps: int = 30
    max_model_calls: int | None = None       # None = only max_steps limits model calls
    max_tool_calls: int = 20
    max_total_tokens: int | None = 200_000   # prompt + completion over the run; None = no limit
    max_run_seconds: float | None = 600      # active execution time, excluding waits for the user
    max_feedback_rounds: int = 3             # per feedback loop, reset when the user replies
    max_context_chars: int = 60_000          # older history is compacted beyond this size
    enabled_tools: list[str] | None = None


class UserReply(BaseModel):
    text: str | None = None
    answers: dict[str, str] = Field(default_factory=dict)  # question_id -> answer
    route_choice: str | None = None
    file_ids: list[str] = Field(default_factory=list)


class WaitQuestion(BaseModel):
    question_id: str
    text: str
    reason: str | None = None
    field: str | None = None


class WaitOption(BaseModel):
    route: str
    label: str


class Wait(BaseModel):
    """What a waiting run needs from the user. resume_run must quote wait_id."""

    wait_id: str
    kind: Literal["clarification", "material_request", "route_selection"]
    questions: list[WaitQuestion] = Field(default_factory=list)
    options: list[WaitOption] = Field(default_factory=list)
    comparison: str | None = None


class RunUsage(BaseModel):
    """Authoritative totals for a run. Includes every retry; do not re-add trace step usage."""

    steps: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    tool_attempts: int = 0                   # including retries
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tokens_complete: bool = True             # False if any model call did not report tokens
    estimated_cost: str | None = "0"         # None once any model call had no price
    cost_currency: str = "CNY"
    duration_ms: int = 0                     # active time, excluding waits for the user


CompletionReason = Literal["finished", "budget_exhausted", "failed", "cancelled"]


class Citation(BaseModel):
    source_id: str
    location: str                            # the sentence or section of the answer that relies on it


class FinalOutput(BaseModel):
    answer: str
    output_refs: list[str] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    open_issues: list[str] = Field(default_factory=list)
    case_version: int
    completion_reason: CompletionReason = "finished"
    partial: bool = False                    # True when the run stopped before finish


class RunInfo(BaseModel):
    conversation_route: Literal["consultation", "case_work"] = "consultation"
    schema_version: str = "0.5"
    run_id: str
    case_id: str
    case_version: int
    experiment_run_id: str | None = None
    status: RunStatus
    wait: Wait | None = None
    final: FinalOutput | None = None
    usage: RunUsage = Field(default_factory=RunUsage)
    error: str | None = None
    stale: bool = False   # final was built on an older case_version than the case has now


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
    event_id: str                     # f"{run_id}:{step_id}", unique across runs
    case_id: str
    run_id: str
    step_id: int                      # monotonic within the run
    parent_step_id: int | None = None # for tool steps: the model_call that requested them
    step_type: StepType
    tool_name: str | None = None
    arguments: dict[str, Any] | None = None
    input_ref: str | None = None
    result_summary: str = ""
    result_ref: str | None = None     # full result, readable with AgentRunner.get_step_result
    source_refs: list[SourceRef] = Field(default_factory=list)
    status: StepStatus
    error: ToolError | None = None
    attempt: int = 1                  # attempts used, including retries
    started_at: str
    finished_at: str
    duration_ms: int = 0
    token_usage: TokenUsage | None = None
    cost: CostInfo | None = None
    case_version: int
    output_version: int | None = None
    decision_note: str | None = None


class TraceExport(BaseModel):
    schema_version: str = "0.5"
    run_id: str
    steps: list[TraceStep]
    next_cursor: int | None           # pass back as cursor; None when nothing more is stored yet
    complete: bool                    # run is terminal and every step has been returned


class ToolManifestEntry(BaseModel):
    name: str
    owner: str
    stages: list[str]
    schema_sha256: str


class RuntimeManifest(BaseModel):
    schema_version: str = "0.5"
    runtime_version: str
    prompt_sha256: str
    tool_schema_sha256: str           # over all registered and built-in tool specs
    tools: list[ToolManifestEntry]
    model: dict[str, Any]             # client class and default model; never credentials
    default_config: RunConfig
