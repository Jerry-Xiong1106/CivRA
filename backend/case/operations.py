"""Business operation schema catalog; not an Agent tool registry."""

from __future__ import annotations

from .materials.contracts import BuildEvidenceDirectoryRequest, CheckEvidenceData, CheckEvidenceRequest, EvidenceDirectoryData, ParseDocumentsData, ParseDocumentsRequest
from .models import BusinessOutput, BusinessResult
from .calculation.contracts import CalculateCostsRequest, CalculationData
from .planning.contracts import CompareRoutesData, CompareRoutesRequest, RouteWorkflowData, RouteWorkflowRequest
from .drafting.contracts import DraftDocumentRequest
from typing import Protocol
from .intake.contracts import UpdateCaseData, UpdateCaseRequest
from .review.contracts import VerificationData, VerifyOutputRequest
from .presentation.contracts import VisualizationData, VisualizationRequest


class BusinessTools(Protocol):
    """Signatures only. No business operation is claimed implemented by this protocol."""

    def parse_documents(self, request: ParseDocumentsRequest) -> BusinessResult[ParseDocumentsData]: ...
    def check_evidence(self, request: CheckEvidenceRequest) -> BusinessResult[CheckEvidenceData]: ...
    def calculate_costs(self, request: CalculateCostsRequest) -> BusinessResult[CalculationData]: ...
    def compare_routes(self, request: CompareRoutesRequest) -> BusinessResult[CompareRoutesData]: ...
    def draft_document(self, request: DraftDocumentRequest) -> BusinessResult[BusinessOutput]: ...
    def verify_output(self, request: VerifyOutputRequest) -> BusinessResult[VerificationData]: ...
    def update_case(self, request: UpdateCaseRequest) -> BusinessResult[UpdateCaseData]: ...
    def build_evidence_directory(self, request: BuildEvidenceDirectoryRequest) -> BusinessResult[EvidenceDirectoryData]: ...
    def get_route_workflow(self, request: RouteWorkflowRequest) -> BusinessResult[RouteWorkflowData]: ...
    def build_visualization(self, request: VisualizationRequest) -> BusinessResult[VisualizationData]: ...


REQUEST_MODELS = {
    "parse_documents": ParseDocumentsRequest,
    "check_evidence": CheckEvidenceRequest,
    "calculate_costs": CalculateCostsRequest,
    "compare_routes": CompareRoutesRequest,
    "draft_document": DraftDocumentRequest,
    "verify_output": VerifyOutputRequest,
    "update_case": UpdateCaseRequest,
    "build_evidence_directory": BuildEvidenceDirectoryRequest,
    "get_route_workflow": RouteWorkflowRequest,
    "build_visualization": VisualizationRequest,
}


RESULT_MODELS = {
    "parse_documents": BusinessResult[ParseDocumentsData],
    "check_evidence": BusinessResult[CheckEvidenceData],
    "calculate_costs": BusinessResult[CalculationData],
    "compare_routes": BusinessResult[CompareRoutesData],
    "draft_document": BusinessResult[BusinessOutput],
    "verify_output": BusinessResult[VerificationData],
    "update_case": BusinessResult[UpdateCaseData],
    "build_evidence_directory": BusinessResult[EvidenceDirectoryData],
    "get_route_workflow": BusinessResult[RouteWorkflowData],
    "build_visualization": BusinessResult[VisualizationData],
}


BUSINESS_STAGES = ("consultation", "route_selection", "negotiation", "mediation", "litigation")

# Business eligibility is shared by direct callers and native tool policies.
OPERATION_STAGES = {name: BUSINESS_STAGES for name in REQUEST_MODELS}
OPERATION_STAGES.update(
    draft_document=("litigation",), build_evidence_directory=("litigation",),
    verify_output=(*BUSINESS_STAGES, "closed"),
)
