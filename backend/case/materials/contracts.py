"""Business request/result contracts for materials."""

from __future__ import annotations

from ..contracts import CaseRequest, SnapshotRequest
from ..models import ContractModel, Evidence, Fact, Finding, Identifier, MaterialRef, PositiveInt, SuggestedQuestion
from pydantic import Field, model_validator
from typing import Literal


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
