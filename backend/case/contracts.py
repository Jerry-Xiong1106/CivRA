"""Python-callable request/result contracts for case and business operations."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import Field, JsonValue, model_validator

from .models import (
    SCHEMA_VERSION, BusinessOutput, BusinessResult, CaseSnapshot, Claim,
    ContractModel, Evidence, Fact, Finding, Identifier, MaterialRef, Money,
    PositiveInt, Route, SourcePointer, SuggestedQuestion, UserStatement, UtcTimestamp,
)
from .route_models import (
    RouteCaseContext, RouteCostItem, RouteDimension, RouteExpenseInput,
    RouteGoalFocus, RouteSourceBinding, RouteSourceCheck,
)
from .workflow_models import WorkflowDateInput, WorkflowEdge, WorkflowProgressRecord, WorkflowSourceCheck, WorkflowSourceNote


class InvocationContext(ContractModel):
    schema_version: Literal["0.1"] = SCHEMA_VERSION
    request_id: Identifier
    case_id: Identifier
    experiment_run_id: Identifier | None
    deadline_at: UtcTimestamp | None


class CaseRequest(ContractModel):
    schema_version: Literal["0.1"] = SCHEMA_VERSION
    context: InvocationContext
    case_version: PositiveInt


class SnapshotRequest(CaseRequest):
    case_snapshot: CaseSnapshot

    @model_validator(mode="after")
    def matching_snapshot(self):
        if (self.context.case_id, self.case_version) != (self.case_snapshot.case_id, self.case_snapshot.case_version):
            raise ValueError("request identity/version does not match case_snapshot")
        return self


class ParseDocumentsRequest(CaseRequest):
    file_ids: list[Identifier] = Field(min_length=1)
    file_versions: dict[Identifier, Identifier] = Field(default_factory=dict)
    ocr_mode: Literal["auto", "always", "never"] = "auto"

    @model_validator(mode="after")
    def unique_files(self):
        if len(self.file_ids) != len(set(self.file_ids)):
            raise ValueError("file_ids must be unique")
        if set(self.file_versions)-set(self.file_ids):
            raise ValueError("file_versions may refer only to requested file_ids")
        return self


class ExtractedText(ContractModel):
    material_ref: MaterialRef
    text: str
    extraction_method: Literal["text", "ocr"]
    warnings: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    engine: str | None = None
    engine_version: str | None = None


class ParseDocumentsData(ContractModel):
    documents: list[Evidence]
    text_segments: list[ExtractedText]
    extracted_facts: list[Fact]
    warnings: list[str]


class CheckEvidenceRequest(SnapshotRequest):
    pass


class CheckEvidenceData(ContractModel):
    missing_facts: list[Finding]
    conflicts: list[Finding]
    duplicates: list[Finding]
    suggested_questions: list[SuggestedQuestion]
    material_gaps: list[Finding] = Field(default_factory=list)
    rule_profile: Identifier = "generic_v1"
    checks_performed: list[Identifier] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class BuildEvidenceDirectoryRequest(SnapshotRequest):
    pass


class EvidenceDirectoryEntry(ContractModel):
    number: PositiveInt
    evidence_id: Identifier
    file_id: Identifier
    file_version: Identifier
    file_name: Identifier
    document_type: Identifier
    material_refs: list[MaterialRef]
    fact_ids: list[Identifier]
    claim_ids: list[Identifier]
    proof_purposes: list[str]
    review_status: Literal["unreviewed", "available", "needs_confirmation"]
    warnings: list[str]


class EvidenceDirectoryData(ContractModel):
    entries: list[EvidenceDirectoryEntry]
    excluded_evidence_ids: list[Identifier]
    supplementary_checklist: list[Finding]
    suggested_questions: list[SuggestedQuestion]
    rule_profile: Identifier
    limitations: list[str]


class CaseAuditRecord(ContractModel):
    case_id: Identifier
    case_version: PositiveInt
    request_id: Identifier | None
    change_reason: Identifier
    saved_at: UtcTimestamp
    changed_statement_ids: list[Identifier] = Field(default_factory=list)
    changed_fact_ids: list[Identifier] = Field(default_factory=list)
    changed_evidence_ids: list[Identifier] = Field(default_factory=list)
    changed_claim_ids: list[Identifier] = Field(default_factory=list)


class CalculateCostsRequest(CaseRequest):
    calculation_type: Identifier
    rule_version: Identifier
    inputs: dict[str, JsonValue]
    rule_refs: list[SourcePointer] = Field(default_factory=list)


class CalculationData(ContractModel):
    calculation_type: Identifier
    rule_version: Identifier
    amounts: dict[str, Money]
    formula: Identifier
    inputs: dict[str, JsonValue]
    assumptions: list[str]
    rule_refs: list[SourcePointer]
    unresolved_inputs: list[Identifier]


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


class DraftDocumentRequest(SnapshotRequest):
    confirmed_fact_ids: list[Identifier]
    route: Literal["litigation"]
    template_id: Identifier
    template_version: Identifier
    source_refs: list[SourcePointer]
    export_formats: list[Literal["md", "docx", "pdf"]] = Field(default_factory=lambda: ["md"])

    @model_validator(mode="after")
    def litigation_and_confirmed_facts(self):
        if self.case_snapshot.stage != "litigation" or self.case_snapshot.selected_route != "litigation":
            raise ValueError("drafting requires the user's litigation selection")
        facts = {item.fact_id: item for item in self.case_snapshot.facts}
        if len(self.confirmed_fact_ids) != len(set(self.confirmed_fact_ids)):
            raise ValueError("confirmed_fact_ids must be unique")
        for fact_id in self.confirmed_fact_ids:
            if fact_id not in facts or facts[fact_id].confirmation_status != "confirmed" or facts[fact_id].superseded_by:
                raise ValueError("draft inputs may select only confirmed facts")
        if not self.export_formats or len(set(self.export_formats)) != len(self.export_formats):
            raise ValueError("export_formats must be nonempty and unique")
        if len({pointer.source_id for pointer in self.source_refs})!=len(self.source_refs):
            raise ValueError("each drafting source_id must identify exactly one source version")
        return self


class VerifyOutputRequest(SnapshotRequest):
    candidate_output: BusinessOutput
    cited_sources: list[SourcePointer]

    @model_validator(mode="after")
    def same_case_output(self):
        if self.candidate_output.case_id != self.case_snapshot.case_id:
            raise ValueError("candidate_output belongs to another case")
        if self.candidate_output.case_version > self.case_version:
            raise ValueError("candidate_output refers to a future case version")
        # Older outputs are allowed: detecting stale output is a verifier responsibility.
        return self


class VerificationData(ContractModel):
    findings: list[Finding]
    checks_performed: list[Identifier]
    checks_not_performed: list[Identifier]
    model_usage: dict[str, JsonValue] | None = None
    citation_audit: dict[str, JsonValue] | None = None
    target_output_id: str | None = None
    target_output_version: int | None = None
    target_digest: str | None = None
    review_scope: str = "规则检查；语义及法律适用按实际执行范围报告"
    source_checks: list[dict[str, JsonValue]] = Field(default_factory=list)
    correction_tasks: list[dict[str, JsonValue]] = Field(default_factory=list)
    disposition: Literal["needs_correction", "needs_review", "checks_passed"] = "needs_review"


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


class VisualizationRequest(SnapshotRequest):
    chart_type: Literal["timeline", "evidence_graph"]


class VisualizationData(ContractModel):
    chart_type: str
    records: list[dict[str, JsonValue]]
    links: list[dict[str, JsonValue]]
    sample_size: int
    missing_values: int
    scope: str


class CaseChanges(ContractModel):
    upsert_statements: list[UserStatement] = Field(default_factory=list)
    upsert_facts: list[Fact] = Field(default_factory=list)
    upsert_evidence: list[Evidence] = Field(default_factory=list)
    upsert_claims: list[Claim] = Field(default_factory=list)

    @model_validator(mode="after")
    def nonempty(self):
        if not (self.upsert_statements or self.upsert_facts or self.upsert_evidence or self.upsert_claims):
            raise ValueError("changes must contain at least one operation")
        for items, key in [(self.upsert_statements, "message_id"), (self.upsert_facts, "fact_id"),
                           (self.upsert_evidence, "evidence_id"), (self.upsert_claims, "claim_id")]:
            if len(items) != len({getattr(item, key) for item in items}):
                raise ValueError(f"duplicate {key} in changes")
        return self


class UpdateCaseRequest(ContractModel):
    schema_version: Literal["0.1"] = SCHEMA_VERSION
    context: InvocationContext
    expected_case_version: PositiveInt
    changes: CaseChanges
    change_reason: Identifier


class UpdateCaseData(ContractModel):
    case_snapshot: CaseSnapshot
    changed_fact_ids: list[Identifier]
    changed_evidence_ids: list[Identifier]
    changed_claim_ids: list[Identifier]
    changed_statement_ids: list[Identifier] = Field(default_factory=list)


class BusinessTools(Protocol):
    """Signatures only. No business operation is claimed implemented by this protocol."""

    def parse_documents(self, request: ParseDocumentsRequest) -> BusinessResult[ParseDocumentsData]: ...
    def check_evidence(self, request: CheckEvidenceRequest) -> BusinessResult[CheckEvidenceData]: ...
    def calculate_costs(self, request: CalculateCostsRequest) -> BusinessResult[CalculationData]: ...
    def compare_routes(self, request: CompareRoutesRequest) -> BusinessResult[CompareRoutesData]: ...
    def draft_document(self, request: DraftDocumentRequest) -> BusinessResult[BusinessOutput]: ...
    def verify_output(self, request: VerifyOutputRequest) -> BusinessResult[VerificationData]: ...
    def update_case(self, request: UpdateCaseRequest) -> BusinessResult[UpdateCaseData]: ...
    def build_evidence_directory(self, request: BuildEvidenceDirectoryRequest) -> BusinessResult[EvidenceDirectoryData]: ...
    def get_route_workflow(self, request: RouteWorkflowRequest) -> BusinessResult[RouteWorkflowData]: ...
    def build_visualization(self, request: VisualizationRequest) -> BusinessResult[VisualizationData]: ...


class CaseRepository(Protocol):
    """Member 1 supplies persistence. An update must atomically compare the version."""

    def get_case(self, case_id: str, version: int | None = None) -> CaseSnapshot: ...
    def replace_case(self, snapshot: CaseSnapshot, *, expected_case_version: int,
                     audit: CaseAuditRecord | None = None) -> CaseSnapshot: ...


class IdempotentCaseRepository(CaseRepository, Protocol):
    """A mutation, its audit and its idempotent response are committed atomically."""

    def find_update(self, case_id: str, request_id: str, request_digest: str) -> BusinessResult[UpdateCaseData] | None: ...
    def commit_update(self, snapshot: CaseSnapshot, *, expected_case_version: int,
                      audit: CaseAuditRecord, request_digest: str,
                      result: BusinessResult[UpdateCaseData],
                      deadline_at: UtcTimestamp | None = None) -> BusinessResult[UpdateCaseData]: ...


class MaterialReader(Protocol):
    def read_file(self, case_id: str, file_id: str, file_version: str) -> bytes: ...


REQUEST_MODELS = {
    "parse_documents": ParseDocumentsRequest,
    "check_evidence": CheckEvidenceRequest,
    "calculate_costs": CalculateCostsRequest,
    "compare_routes": CompareRoutesRequest,
    "draft_document": DraftDocumentRequest,
    "verify_output": VerifyOutputRequest,
    "update_case": UpdateCaseRequest,
    "build_evidence_directory": BuildEvidenceDirectoryRequest,
    "get_route_workflow": RouteWorkflowRequest,
    "build_visualization": VisualizationRequest,
}

RESULT_MODELS = {
    "parse_documents": BusinessResult[ParseDocumentsData],
    "check_evidence": BusinessResult[CheckEvidenceData],
    "calculate_costs": BusinessResult[CalculationData],
    "compare_routes": BusinessResult[CompareRoutesData],
    "draft_document": BusinessResult[BusinessOutput],
    "verify_output": BusinessResult[VerificationData],
    "update_case": BusinessResult[UpdateCaseData],
    "build_evidence_directory": BusinessResult[EvidenceDirectoryData],
    "get_route_workflow": BusinessResult[RouteWorkflowData],
    "build_visualization": BusinessResult[VisualizationData],
}
