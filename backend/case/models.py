"""Member 3's versioned domain models; independent of storage and Agent runtime."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import (
    AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, JsonValue,
    PlainSerializer, StrictBool, StrictInt, StringConstraints, model_validator,
)

SCHEMA_VERSION = "0.1"
IMPLEMENTATION_VERSION = "case-tools-0.6.0"
Identifier = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]
PositiveInt = Annotated[StrictInt, Field(ge=1)]
Offset = Annotated[StrictInt, Field(ge=0)]
Route = Literal["negotiation", "mediation", "litigation"]
Stage = Literal["consultation", "route_selection", "negotiation", "mediation", "litigation", "closed"]


def _timestamp_input(value):
    if not isinstance(value, (str, datetime)):
        raise ValueError("timestamp must be an ISO 8601 string or aware datetime")
    return value


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(timezone.utc)


UtcTimestamp = Annotated[
    datetime, BeforeValidator(_timestamp_input), AfterValidator(_utc),
    PlainSerializer(lambda value: value.isoformat().replace("+00:00", "Z"), return_type=str, when_used="json"),
]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Money(ContractModel):
    amount: Annotated[str, StringConstraints(strict=True, pattern=r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")]
    currency: Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Z]{3}$")]

    @property
    def decimal(self) -> Decimal:
        return Decimal(self.amount)


class MaterialRef(ContractModel):
    """Page is 1-based; spans index extracted Unicode text, start-inclusive/end-exclusive."""

    file_id: Identifier
    file_version: Identifier
    page: PositiveInt | None = None
    paragraph: Identifier | None = None
    span_start: Offset | None = None
    span_end: Offset | None = None
    quote: str | None = None
    extraction_version: Identifier | None = None
    polygon: list[list[float]] | None = None

    @model_validator(mode="after")
    def valid_location(self):
        if self.polygon is not None:
            import math
            if len(self.polygon) != 4 or any(len(point) != 2 or
                any(not math.isfinite(value) or value < 0 for value in point) for point in self.polygon):
                raise ValueError("polygon must contain four finite, nonnegative x/y points")
        if (self.span_start is None) != (self.span_end is None):
            raise ValueError("span_start and span_end must be supplied together")
        if self.span_start is not None and self.span_end <= self.span_start:
            raise ValueError("span_end must be greater than span_start")
        if self.page is None and self.paragraph is None and self.span_start is None:
            raise ValueError("a material reference needs a page, paragraph, or span")
        return self


class SourcePointer(ContractModel):
    """Foreign key to Member 2's source record, not a replacement SourceRef definition."""

    source_id: Identifier
    source_version: Identifier | None


class UserStatement(ContractModel):
    message_id: Identifier
    actor_id: Identifier
    text: Identifier
    created_at: UtcTimestamp


class Confirmation(ContractModel):
    message_id: Identifier
    confirmed_by: Identifier
    confirmed_at: UtcTimestamp


class Fact(ContractModel):
    fact_id: Identifier
    field: Identifier
    value_type: Literal["text", "date", "money", "integer", "boolean"]
    value: JsonValue
    provenance: Literal["user_statement", "document_record", "derived"]
    confirmation_status: Literal["unconfirmed", "confirmed", "disputed"]
    statement_refs: list[Identifier] = Field(default_factory=list)
    evidence_ids: list[Identifier] = Field(default_factory=list)
    material_refs: list[MaterialRef] = Field(default_factory=list)
    source_refs: list[SourcePointer] = Field(default_factory=list)
    derivation_ref: Identifier | None = None
    confirmation: Confirmation | None = None
    superseded_by: Identifier | None = None

    @model_validator(mode="after")
    def validate_value_and_provenance(self):
        if self.value is not None:
            if self.value_type == "text" and (not isinstance(self.value, str) or not self.value.strip()):
                raise ValueError("text facts need a nonempty string")
            if self.value_type == "date":
                if not isinstance(self.value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", self.value):
                    raise ValueError("date facts use YYYY-MM-DD strings")
                date.fromisoformat(self.value)
            if self.value_type == "money":
                self.value = Money.model_validate(self.value).model_dump(mode="json")
            if self.value_type == "integer" and type(self.value) is not int:
                raise ValueError("integer facts need an integer, excluding bool")
            if self.value_type == "boolean" and type(self.value) is not bool:
                raise ValueError("boolean facts need true or false")
        if self.provenance == "user_statement" and not self.statement_refs:
            raise ValueError("user statements need statement_refs")
        if self.provenance == "document_record" and (not self.material_refs or not self.evidence_ids):
            raise ValueError("document facts need material_refs and evidence_ids")
        if self.provenance == "derived" and not self.derivation_ref:
            raise ValueError("derived facts need a derivation_ref")
        if self.confirmation_status == "confirmed":
            if self.value is None or self.confirmation is None:
                raise ValueError("confirmed facts need a known value and confirmation record")
        elif self.confirmation is not None:
            raise ValueError("only confirmed facts have a current confirmation record")
        return self


class Evidence(ContractModel):
    evidence_id: Identifier
    file_id: Identifier
    file_version: Identifier
    file_name: Identifier
    media_type: Identifier
    document_type: Identifier
    content_sha256: Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]
    material_refs: Annotated[list[MaterialRef], Field(min_length=1)]
    proves_fact_ids: list[Identifier] = Field(default_factory=list)
    status: Literal["unreviewed", "available", "needs_confirmation", "excluded"] = "unreviewed"
    duplicate_of: Identifier | None = None
    exclusion: Confirmation | None = None
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def references_own_file(self):
        if any((ref.file_id, ref.file_version) != (self.file_id, self.file_version) for ref in self.material_refs):
            raise ValueError("evidence material_refs must identify its own file and version")
        if self.duplicate_of == self.evidence_id:
            raise ValueError("evidence cannot duplicate itself")
        if (self.status == "excluded") != (self.exclusion is not None):
            raise ValueError("excluded evidence requires an explicit exclusion confirmation")
        return self


class Claim(ContractModel):
    claim_id: Identifier
    kind: Identifier
    description: Identifier
    amount: Money | None
    status: Literal["proposed", "user_confirmed", "withdrawn"] = "proposed"
    fact_ids: list[Identifier] = Field(default_factory=list)
    evidence_ids: list[Identifier] = Field(default_factory=list)
    source_refs: list[SourcePointer] = Field(default_factory=list)


class SuggestedQuestion(ContractModel):
    question_id: Identifier
    text: Identifier
    field: Identifier
    reason: Identifier
    fact_ids: list[Identifier] = Field(default_factory=list)
    evidence_ids: list[Identifier] = Field(default_factory=list)


class Finding(ContractModel):
    finding_id: Identifier
    category: Literal["missing_fact", "missing_evidence", "conflict", "duplicate", "calculation", "citation", "required_field", "version"]
    severity: Literal["info", "warning", "error"]
    message: Identifier
    affected_fields: list[Identifier] = Field(default_factory=list)
    fact_ids: list[Identifier] = Field(default_factory=list)
    evidence_ids: list[Identifier] = Field(default_factory=list)
    material_refs: list[MaterialRef] = Field(default_factory=list)
    output_location: str | None = None
    source_refs: list[SourcePointer] = Field(default_factory=list)
    suggested_correction: str | None = None


class OutputLink(ContractModel):
    output_location: Identifier
    fact_ids: list[Identifier] = Field(default_factory=list)
    evidence_ids: list[Identifier] = Field(default_factory=list)
    source_refs: list[SourcePointer] = Field(default_factory=list)


class BusinessOutput(ContractModel):
    schema_version: Literal["0.1"] = SCHEMA_VERSION
    output_id: Identifier
    output_version: PositiveInt
    case_id: Identifier
    case_version: PositiveInt
    output_type: Literal["analysis", "evidence_directory", "route_comparison", "workflow", "document", "verification", "visualization"]
    review_status: Literal["unreviewed", "needs_review", "reviewed"] = "unreviewed"
    text: str | None = None
    artifact_id: Identifier | None = None
    structured_data: dict[str, JsonValue] | None = None
    missing_fields: list[Identifier] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    links: list[OutputLink] = Field(default_factory=list)
    source_refs: list[SourcePointer] = Field(default_factory=list)
    template_id: Identifier | None = None
    template_version: Identifier | None = None

    @model_validator(mode="after")
    def validate_content(self):
        if not (self.text and self.text.strip()) and self.artifact_id is None and self.structured_data is None:
            raise ValueError("an output needs text, artifact_id, or structured_data")
        if (self.template_id is None) != (self.template_version is None):
            raise ValueError("template_id and template_version must be supplied together")
        return self

    def is_stale_for(self, case: CaseSnapshot) -> bool:
        if self.case_id != case.case_id:
            raise ValueError("output belongs to another case")
        if self.case_version > case.case_version:
            raise ValueError("output refers to a future case version")
        return self.case_version < case.case_version


class CaseSnapshot(ContractModel):
    schema_version: Literal["0.1"] = SCHEMA_VERSION
    case_id: Identifier
    case_version: PositiveInt
    synthetic: StrictBool
    dispute_type: Identifier | None
    jurisdiction: Identifier | None
    stage: Stage
    selected_route: Route | None
    user_goals: list[Identifier] = Field(default_factory=list)
    statements: list[UserStatement] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    created_at: UtcTimestamp
    updated_at: UtcTimestamp

    @model_validator(mode="after")
    def validate_graph(self):
        def indexed(items, key):
            result = {getattr(item, key): item for item in items}
            if len(result) != len(items):
                raise ValueError(f"duplicate {key}")
            return result

        facts = indexed(self.facts, "fact_id")
        evidence = indexed(self.evidence, "evidence_id")
        statements = indexed(self.statements, "message_id")
        indexed(self.claims, "claim_id")
        files = {(item.file_id, item.file_version) for item in self.evidence}
        file_metadata = {}
        for item in self.evidence:
            if item.exclusion:
                statement = statements.get(item.exclusion.message_id)
                if statement is None or statement.actor_id != item.exclusion.confirmed_by:
                    raise ValueError("exclusion must reference the confirming user's statement")
                if not statement.created_at <= item.exclusion.confirmed_at <= self.updated_at:
                    raise ValueError("exclusion confirmation time is outside the case history")
            key = (item.file_id, item.file_version)
            metadata = (item.content_sha256, item.file_name, item.media_type)
            if key in file_metadata and file_metadata[key] != metadata:
                raise ValueError("one file version cannot have conflicting file metadata")
            file_metadata[key] = metadata
        if any(item.created_at > self.updated_at for item in self.statements):
            raise ValueError("statements cannot postdate the case update")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.stage in ("negotiation", "mediation", "litigation"):
            if self.selected_route != self.stage:
                raise ValueError("an active route stage must match selected_route")
        elif self.stage != "closed" and self.selected_route is not None:
            raise ValueError("selected_route stays null until the user selects a route")
        for item in self.evidence:
            if set(item.proves_fact_ids) - facts.keys():
                raise ValueError("evidence references unknown facts")
            if item.duplicate_of is not None and item.duplicate_of not in evidence:
                raise ValueError("duplicate_of references unknown evidence")
            seen = {item.evidence_id}
            parent = item.duplicate_of
            while parent is not None:
                if parent not in evidence:
                    raise ValueError("duplicate_of references unknown evidence")
                if parent in seen:
                    raise ValueError("duplicate_of contains a cycle")
                seen.add(parent)
                parent = evidence[parent].duplicate_of
        confirmed = {}
        for fact in self.facts:
            if fact.superseded_by:
                seen = {fact.fact_id}
                target = fact.superseded_by
                while target is not None:
                    if target not in facts or target in seen:
                        raise ValueError("superseded_by references an unknown fact or contains a cycle")
                    seen.add(target)
                    replacement = facts[target]
                    if replacement.field != fact.field or replacement.value_type != fact.value_type:
                        raise ValueError("a superseding fact must refer to the same field and value type")
                    if replacement.confirmation_status != "confirmed":
                        raise ValueError("a superseding fact must have an explicit confirmation")
                    target = replacement.superseded_by
            if set(fact.evidence_ids) - evidence.keys() or set(fact.statement_refs) - statements.keys():
                raise ValueError("fact references unknown evidence or statements")
            if any((ref.file_id, ref.file_version) not in files for ref in fact.material_refs):
                raise ValueError("fact references an unknown file version")
            linked_files = {(evidence[key].file_id, evidence[key].file_version) for key in fact.evidence_ids}
            if any((ref.file_id, ref.file_version) not in linked_files for ref in fact.material_refs):
                raise ValueError("fact material_refs must be backed by its evidence_ids")
            if fact.confirmation:
                confirmation = fact.confirmation
                statement = statements.get(confirmation.message_id)
                if statement is None or statement.actor_id != confirmation.confirmed_by:
                    raise ValueError("confirmation must reference the confirming user's statement")
                if not statement.created_at <= confirmation.confirmed_at <= self.updated_at:
                    raise ValueError("confirmation time must fall between statement and case update")
                if fact.superseded_by is None:
                    if fact.field in confirmed and confirmed[fact.field] != fact.value:
                        raise ValueError("conflicting values for a confirmed single-valued field")
                    confirmed[fact.field] = fact.value
        for claim in self.claims:
            if set(claim.fact_ids) - facts.keys() or set(claim.evidence_ids) - evidence.keys():
                raise ValueError("claim references unknown facts or evidence")
        return self


class BusinessError(ContractModel):
    code: Literal["invalid_request", "not_found", "version_conflict", "timeout", "rate_limited", "provider_error", "tool_error", "cancelled", "not_implemented"]
    message: Identifier
    retryable: StrictBool = False
    details: dict[str, JsonValue] | None = None


T = TypeVar("T")


class BusinessResult(ContractModel, Generic[T]):
    schema_version: Literal["0.1"] = SCHEMA_VERSION
    case_id: Identifier | None
    case_version: PositiveInt | None
    status: Literal["ok", "empty", "insufficient", "error"]
    summary: str
    data: T | None
    source_refs: list[SourcePointer] = Field(default_factory=list)
    error: BusinessError | None = None
    output_refs: list[Identifier] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status(self):
        if self.status == "error":
            if self.error is None or self.data is not None:
                raise ValueError("errors require error details and null data")
        elif self.error is not None or self.data is None or self.case_id is None or self.case_version is None:
            raise ValueError("successful business calls need data, case identity/version, and null error")
        return self
