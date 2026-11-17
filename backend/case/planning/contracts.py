"""Business request/result contracts for planning."""

from __future__ import annotations

from ..calculation.contracts import CalculationData
from ..models import ContractModel, Finding, Identifier, Money, Route, SourcePointer
from pydantic import Field, model_validator
from typing import Literal
from ..case_context import RouteCaseContext
from .route_models import RouteCostItem, RouteDimension, RouteExpenseInput, RouteGoalFocus, RouteSourceBinding, RouteSourceCheck
from ..contracts import SnapshotRequest
from .workflow_models import WorkflowDateInput, WorkflowEdge, WorkflowProgressRecord, WorkflowSourceCheck, WorkflowSourceNote


class CompareRoutesRequest(SnapshotRequest):
    available_authorities: list[SourcePointer] = Field(max_length=30)
    calculations: list[CalculationData] = Field(max_length=30)
    user_goals: list[Identifier] = Field(max_length=30)
    source_bindings: list[RouteSourceBinding] = Field(default_factory=list, max_length=30)
    user_expenses: list[RouteExpenseInput] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def binding_identity(self):
        if len({item.binding_id for item in self.source_bindings}) != len(self.source_bindings):
            raise ValueError("binding_id must be unique")
        if any(item.source_ref not in self.available_authorities for item in self.source_bindings):
            raise ValueError("bindings must refer to available_authorities with matching version")
        return self


class RouteOption(ContractModel):
    route: Route
    prerequisites: list[str]
    steps: list[str]
    costs: list[Money]
    uncertainties: list[str]
    source_refs: list[SourcePointer]
    dimensions: list[RouteDimension] = Field(default_factory=list)
    cost_items: list[RouteCostItem] = Field(default_factory=list)


class CompareRoutesData(ContractModel):
    options: list[RouteOption]
    assumptions: list[str]
    unresolved_items: list[str]
    implementation_version: str = "civil_comparison_v1"
    case_context: RouteCaseContext | None = None
    source_checks: list[RouteSourceCheck] = Field(default_factory=list)
    goal_focus: list[RouteGoalFocus] = Field(default_factory=list)
    cost_review_issues: list[str] = Field(default_factory=list)


class RouteWorkflowRequest(SnapshotRequest):
    route: Route
    available_authorities: list[SourcePointer] = Field(default_factory=list, max_length=30)
    progress_version: int | None = Field(default=None, ge=0, strict=True)
    source_notes: list[WorkflowSourceNote] = Field(default_factory=list, max_length=30)
    dates: list[WorkflowDateInput] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def unique_annotations(self):
        if len({item.note_id for item in self.source_notes}) != len(self.source_notes):
            raise ValueError("workflow note_id must be unique")
        if len({item.step_id for item in self.dates}) != len(self.dates):
            raise ValueError("at most one explicitly selected date per step")
        if any(item.source_ref not in self.available_authorities for item in self.source_notes):
            raise ValueError("workflow notes must use listed authority versions")
        return self


class WorkflowStep(ContractModel):
    step_id: Identifier
    title: str
    contact: str | None
    prerequisites: list[str]
    materials: list[str]
    completion_condition: str
    next_if_unsolved: list[str]
    source_refs: list[SourcePointer]
    deadline: str | None = None
    deadline_kind: Literal["user_target", "material_record"] | None = None
    deadline_fact_id: str | None = None
    deadline_verified: Literal[False] = False
    conditional: bool = False
    applicability: str = "具体适用条件待核实"
    status: Literal["pending", "in_progress", "completed", "blocked", "not_applicable", "needs_review"] = "pending"
    should_skip: bool = False
    progress: WorkflowProgressRecord | None = None
    finding_ids: list[str] = Field(default_factory=list)
    source_note_ids: list[str] = Field(default_factory=list)


class RouteWorkflowData(ContractModel):
    route: Route
    template_version: str
    steps: list[WorkflowStep]
    unresolved_items: list[str]
    conditional_steps: list[WorkflowStep] = Field(default_factory=list)
    edges: list[WorkflowEdge] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    source_checks: list[WorkflowSourceCheck] = Field(default_factory=list)
    progress_version: int = 0
    progress_is_latest: bool = True
    case_basis_hash: str | None = None
    plan_basis_hash: str | None = None
    pending_step_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
