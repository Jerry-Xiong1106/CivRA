"""Business request/result contracts for review."""

from __future__ import annotations

from ..models import BusinessOutput, ContractModel, Finding, Identifier, SourcePointer
from pydantic import Field, JsonValue, model_validator
from typing import Literal
from ..contracts import SnapshotRequest


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
