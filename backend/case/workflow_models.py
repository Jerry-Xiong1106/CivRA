"""Structured user workflow annotations; no inferred legal deadlines or eligibility."""

from typing import Literal
from pydantic import Field, model_validator
from .models import ContractModel, Identifier, SourcePointer
from .route_models import RouteSourceExcerpt

WORKFLOW_VERSION = "civil_planning_v2"
TaskStatus = Literal["pending", "in_progress", "completed", "blocked", "not_applicable"]


class WorkflowSourceNote(ContractModel):
    note_id: Identifier
    step_id: Identifier
    aspect: Literal["contact", "materials", "conditions", "deadline", "next_action"]
    note: str = Field(min_length=1, max_length=2000)
    source_ref: SourcePointer
    quote: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def pinned(self):
        if self.source_ref.source_version is None or not self.note.strip() or not self.quote.strip():
            raise ValueError("workflow note requires a pinned version and nonblank note/quote")
        return self


class WorkflowDateInput(ContractModel):
    step_id: Identifier
    fact_id: Identifier
    kind: Literal["user_target", "material_record"]


class WorkflowSourceCheck(ContractModel):
    note_id: str
    step_id: str
    aspect: str
    proposed_note: str
    source: RouteSourceExcerpt | None
    status: Literal["original_text_checked", "rejected"]
    semantic_support_checked: Literal[False] = False
    reason: str


class WorkflowProgressRecord(ContractModel):
    step_id: str
    status: TaskStatus
    note: str
    message_id: str
    actor_id: str
    quote: str
    evidence_ids: list[str]
    plan_output_id: str
    plan_basis_hash: str
    case_version: int
    progress_version: int


class WorkflowEdge(ContractModel):
    from_step: str
    to_step: str
    kind: Literal["suggested_order", "conditional"]
    condition: str
