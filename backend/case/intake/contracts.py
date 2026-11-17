"""Business request/result contracts for intake."""

from __future__ import annotations

from ..models import CaseSnapshot, Claim, ContractModel, Evidence, Fact, Identifier, PositiveInt, SCHEMA_VERSION, UserStatement
from pydantic import Field, model_validator
from ..contracts import InvocationContext
from typing import Literal


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
