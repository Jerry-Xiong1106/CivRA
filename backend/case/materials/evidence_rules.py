"""Versioned engineering reminders, separate from statutory evidence requirements."""

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from ..models import ContractModel, Identifier


class MaterialRequirement(ContractModel):
    requirement_id: Identifier
    label: Identifier
    any_of: list[Identifier] = Field(min_length=1)
    reason: Identifier


class EvidenceProfile(ContractModel):
    profile_id: Identifier
    required_fields: dict[str, str] = Field(default_factory=dict)
    claim_materials: dict[str, list[MaterialRequirement]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_rules(self):
        for requirements in self.claim_materials.values():
            ids = [item.requirement_id for item in requirements]
            if len(ids) != len(set(ids)):
                raise ValueError("material requirement ids must be unique within a claim kind")
        return self


class EvidenceRules(ContractModel):
    schema_version: Literal["1"]
    rule_set_version: Identifier
    authority: Literal["engineering_reminders_not_statutory_requirements"]
    decision_notes: list[str]
    profiles: dict[str, EvidenceProfile]


def load_evidence_rules(path=None) -> EvidenceRules:
    source = Path(path) if path else Path(__file__).resolve().parents[3] / "data/config/member3_evidence_rules.json"
    # Invalid/missing configuration fails explicitly; never invent fallback legal rules.
    return EvidenceRules.model_validate_json(source.read_text(encoding="utf-8-sig"))


def profile_for(dispute_type, rules):
    from ..research.knowledge_bridge import normalize_dispute_type
    return rules.profiles.get(normalize_dispute_type(dispute_type), EvidenceProfile(profile_id="generic_v1"))
