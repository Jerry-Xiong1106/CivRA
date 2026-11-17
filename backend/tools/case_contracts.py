"""Caller-supplied arguments and case stages, independent of Agent tools."""

from copy import deepcopy
from typing import Literal

from pydantic import Field, JsonValue, create_model

from ..case.intake.contracts import CaseChanges
from ..case.contracts import InvocationContext
from ..case.operations import REQUEST_MODELS, BUSINESS_STAGES, OPERATION_STAGES
from ..case.models import ContractModel, Identifier, PositiveInt, SCHEMA_VERSION


class ToolInvocation(ContractModel):
    schema_version: Literal["0.1"] = SCHEMA_VERSION
    context: InvocationContext
    case_version: PositiveInt
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


def _argument_models():
    """Preserve business field types while excluding trusted context and messages."""
    protected = {"schema_version", "context", "case_version", "case_snapshot"}
    models = {}
    changes_fields = {
        name: (field.annotation, deepcopy(field))
        for name, field in CaseChanges.model_fields.items()
        if name != "upsert_statements"
    }
    tool_changes = create_model("CaseToolChanges", __base__=ContractModel, **changes_fields)
    for name, request_model in REQUEST_MODELS.items():
        if name == "verify_output":
            fields = {"output_id": (Identifier, ...)}
        else:
            fields = {
                field_name: (field.annotation, deepcopy(field))
                for field_name, field in request_model.model_fields.items()
                if field_name not in protected
            }
            if name == "update_case":
                fields["changes"] = (tool_changes, ...)
        models[name] = create_model(name + "Arguments", __base__=ContractModel, **fields)
    return models


ARGUMENT_MODELS = _argument_models()
