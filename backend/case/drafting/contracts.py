"""Business request/result contracts for drafting."""

from __future__ import annotations

from pydantic import Field, model_validator
from ..models import Identifier, SourcePointer
from typing import Literal
from ..contracts import SnapshotRequest


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
