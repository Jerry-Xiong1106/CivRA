"""Pilot task contracts. Only PublicTask.to_model_input() crosses into a model."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Text = Annotated[str, Field(strict=True, min_length=1, pattern=r"\S")]
Identifier = Annotated[str, Field(strict=True, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")]
Route = Literal["negotiation", "mediation", "litigation"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["0.1"] = "0.1"


class Material(BaseModel):
    model_config = ConfigDict(extra="forbid")
    material_id: Identifier
    title: Text
    text: Text
    locator: Text


class PublicTask(Contract):
    task_id: Identifier
    source_case_id: Identifier
    dataset_version: Text
    split: Literal["development", "final_test"]
    phase: Literal["pilot", "formal"]
    task_type: Literal["static", "interactive"]
    domain: Literal["lending", "leasing", "contract", "tort", "coverage"]
    tags: list[Text] = Field(min_length=1)
    language: Literal["zh-CN"] = "zh-CN"
    initial_input: Text
    user_goal: Text
    selected_route: Route | None = None
    materials: list[Material] = Field(default_factory=list)
    output_requirements: list[Text] = Field(min_length=1)
    provenance: Literal["synthetic", "deidentified_public_case"]
    source_reference: Text

    @model_validator(mode="after")
    def check_task(self):
        if self.phase == "pilot" and self.split != "development":
            raise ValueError("Pilot tasks must remain development data")
        ids = [m.material_id for m in self.materials]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate material_id")
        return self

    def to_model_input(self) -> dict:
        """Explicit allowlist; excludes task classification and provenance hints."""
        return self.model_dump(include={
            "initial_input", "user_goal", "selected_route", "materials", "output_requirements"
        }, mode="json")


class HiddenFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fact_id: Identifier
    disclosure_condition: Text
    accepted_question_examples: list[Text] = Field(min_length=1)
    response: Text


class HiddenTask(Contract):
    task_id: Identifier
    dataset_version: Text
    facts: list[HiddenFact]
    route_choice: Route | None = None

    @model_validator(mode="after")
    def unique_facts(self):
        ids = [f.fact_id for f in self.facts]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate fact_id")
        return self


class ScoreItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: Identifier
    dimension: Literal["task_outcome", "evidence", "citation", "guidance", "behavior"]
    description: Text
    zero: Text
    one: Text
    two: Text
    applicable_modes: list[Literal["direct_llm", "rag", "fixed_workflow", "agent"]] = Field(min_length=1)
    required_for_success: bool
    evidence_refs: list[Text] = Field(default_factory=list)


class GoldTask(Contract):
    task_id: Identifier
    dataset_version: Text
    review_status: Literal["draft", "reviewed"]
    reviewed_by: list[Text] = Field(default_factory=list)
    key_facts: list[Text]
    acceptable_outputs: list[Text] = Field(min_length=1)
    prohibited_inventions: list[Text] = Field(min_length=1)
    authority_refs: list[Text] = Field(default_factory=list)
    scoring_items: list[ScoreItem] = Field(min_length=1)

    @model_validator(mode="after")
    def check_review(self):
        ids = [item.item_id for item in self.scoring_items]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate scoring item_id")
        if self.review_status == "reviewed" and not self.reviewed_by:
            raise ValueError("Reviewed annotations need reviewer identities")
        if not any(i.required_for_success for i in self.scoring_items):
            raise ValueError("At least one task-success condition is required")
        return self


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_records(path: Path, contract: type[Contract]) -> list[Contract]:
    records = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line, object_pairs_hook=_unique_object)
            records.append(contract.model_validate(raw))
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{path}:{line_no}: {exc}") from exc
    if not records:
        raise ValueError(f"Empty dataset: {path}")
    ids = [r.task_id for r in records]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate task_id in {path}")
    return records


def load_public_tasks(path: Path) -> list[PublicTask]:
    """The model-facing loader never opens private annotation files."""
    return read_records(path, PublicTask)


def validate_bundle(public_path: Path, hidden_path: Path, gold_path: Path) -> dict:
    """Controller/scorer-only join; returns counts, never private content."""
    public = load_public_tasks(public_path)
    hidden = {t.task_id: t for t in read_records(hidden_path, HiddenTask)}
    gold = {t.task_id: t for t in read_records(gold_path, GoldTask)}
    ids = {t.task_id for t in public}
    if ids != set(hidden) or ids != set(gold):
        raise ValueError("Public, hidden and gold task IDs must match exactly")
    families = {}
    for task in public:
        h, g = hidden[task.task_id], gold[task.task_id]
        if task.dataset_version != h.dataset_version or task.dataset_version != g.dataset_version:
            raise ValueError(f"Dataset version mismatch: {task.task_id}")
        if task.task_type == "static" and (h.facts or h.route_choice):
            raise ValueError(f"Static task cannot require disclosure: {task.task_id}")
        if task.selected_route and h.route_choice and task.selected_route != h.route_choice:
            raise ValueError(f"Conflicting route choices: {task.task_id}")
        if task.split == "final_test" and g.review_status != "reviewed":
            raise ValueError(f"Final-test annotations need review: {task.task_id}")
        splits = families.setdefault(task.source_case_id, set())
        splits.add(task.split)
        if len(splits) > 1:
            raise ValueError(f"Case family crosses data splits: {task.source_case_id}")
    return {
        "tasks": len(public),
        "static": sum(t.task_type == "static" for t in public),
        "interactive": sum(t.task_type == "interactive" for t in public),
        "draft_annotations": sum(t.review_status == "draft" for t in gold.values()),
    }
