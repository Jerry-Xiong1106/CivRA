"""Business request/result contracts for calculation."""

from __future__ import annotations

from ..contracts import CaseRequest
from ..models import ContractModel, Identifier, Money, SourcePointer
from pydantic import Field, JsonValue


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
