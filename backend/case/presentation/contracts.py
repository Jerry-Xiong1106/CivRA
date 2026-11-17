"""Business request/result contracts for presentation."""

from __future__ import annotations

from ..models import ContractModel
from pydantic import JsonValue
from typing import Literal
from ..contracts import SnapshotRequest


class VisualizationRequest(SnapshotRequest):
    chart_type: Literal["timeline", "evidence_graph"]


class VisualizationData(ContractModel):
    chart_type: str
    records: list[dict[str, JsonValue]]
    links: list[dict[str, JsonValue]]
    sample_size: int
    missing_values: int
    scope: str
