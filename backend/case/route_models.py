"""Additive, Python-callable contracts for the five route comparison dimensions."""

from typing import Literal

from pydantic import Field, model_validator

from .models import ContractModel, Finding, Identifier, Money, Route, SourcePointer


Dimension = Literal["materials", "costs", "time_steps", "agreement_performance", "risks"]
COMPARISON_VERSION = "case-routes-0.2.0"


class RouteSourceBinding(ContractModel):
    binding_id: Identifier
    route: Route
    dimension: Dimension
    statement: str = Field(min_length=1, max_length=2000)
    source_ref: SourcePointer
    quote: str = Field(min_length=1, max_length=2000)
    conditions: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def explicit_version(self):
        if self.source_ref.source_version is None or not self.statement.strip() or not self.quote.strip():
            raise ValueError("binding requires a pinned source and nonblank statement/quote")
        return self


class RouteExpenseInput(ContractModel):
    route: Route
    label: str = Field(min_length=1, max_length=200)
    fact_id: Identifier

    @model_validator(mode="after")
    def meaningful_label(self):
        if not self.label.strip():
            raise ValueError("expense label must not be blank")
        return self


class RouteSourceExcerpt(ContractModel):
    source_ref: SourcePointer
    source_type: Literal["statute", "case"]
    title: str
    locator: str | dict
    url: str | None
    quote: str
    quote_start: int
    quote_end: int
    text_sha256: str
    metadata: dict
    warnings: list[str]


class RouteSourceCheck(ContractModel):
    binding_id: str
    route: Route
    dimension: Dimension
    proposed_statement: str
    conditions: list[str]
    status: Literal["original_text_checked", "rejected"]
    semantic_support_checked: Literal[False] = False
    source: RouteSourceExcerpt | None
    reason: str


class RouteCostItem(ContractModel):
    kind: Literal["acceptance_fee_estimate", "user_declared_expense"]
    label: str
    amount: Money
    assumptions: list[str]
    source_refs: list[SourcePointer] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list)
    calculation_indexes: list[int] = Field(default_factory=list)


class RouteDimension(ContractModel):
    dimension: Dimension
    summary: str
    fact_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    finding_ids: list[str] = Field(default_factory=list)
    source_binding_ids: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)


class RouteCaseContext(ContractModel):
    confirmed_fact_ids: list[str]
    pending_fact_ids: list[str]
    evidence_ids: list[str]
    excluded_evidence_ids: list[str]
    claim_ids: list[str]
    findings: list[Finding]
    evidence_rule_profile: str
    limitations: list[str]


class RouteGoalFocus(ContractModel):
    goal: str
    origin: Literal["request", "case", "both"]
    dimensions: list[Dimension]
    question: str
