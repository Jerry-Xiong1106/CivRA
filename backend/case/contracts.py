"""Shared request context, version contract and repository protocols."""

from __future__ import annotations

from .models import BusinessResult, CaseSnapshot, ContractModel, Identifier, PositiveInt, SCHEMA_VERSION, UtcTimestamp
from pydantic import Field, model_validator
from typing import Literal, Protocol
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .intake.contracts import UpdateCaseData


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
