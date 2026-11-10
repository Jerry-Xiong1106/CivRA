"""Shared synchronous tool entry point for Runtime and evaluation callers."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from time import monotonic
from typing import Literal
import warnings

from pydantic import Field, JsonValue, ValidationError, create_model

from .adapters import to_evaluation_result
from .contracts import CaseChanges, InvocationContext, REQUEST_MODELS, RESULT_MODELS
from .models import BusinessError, BusinessResult, ContractModel, Identifier, PositiveInt, SCHEMA_VERSION
from .repository import CaseNotFound, VersionConflict


class ToolInvocation(ContractModel):
    schema_version: Literal["0.1"] = SCHEMA_VERSION
    context: InvocationContext
    case_version: PositiveInt
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


BUSINESS_STAGES = ["consultation", "route_selection", "negotiation", "mediation", "litigation"]
DESCRIPTIONS = {
    "parse_documents": "读取本案已上传文件，提取文字及候选事实，返回原文位置和 OCR 警告。",
    "check_evidence": "检查缺失事实、冲突、相同文件及诉求材料缺口，返回位置与建议问题。",
    "calculate_costs": "按明确输入和版本化规则执行 Decimal 计算，返回公式、依据与未确定参数。",
    "compare_routes": "比较协商、调解和诉讼的条件、步骤、成本及未知项，供用户选择。",
    "get_route_workflow": "根据用户已选路线生成步骤、材料和完成条件，保留尚未核实的信息。",
    "draft_document": "在用户选择诉讼后按版本化模板生成草稿，使用选定的已确认事实并保留缺失项。",
    "verify_output": "核验已保存输出的版本、字段、事实证据链接、计算和来源，列明未执行的语义检查。",
    "update_case": "按预期版本保存事实、证据和诉求变更，确认操作须引用应用已保存的用户消息。",
    "build_evidence_directory": "在诉讼阶段生成证据目录、拟证明事项和补证清单，保留冲突与未确认提示。",
    "build_visualization": "返回当前案件时间线或证据关系数据，保留来源、未确认及已取代状态。",
}
WRITING_TOOLS = {
    "parse_documents", "compare_routes", "get_route_workflow", "draft_document",
    "verify_output", "update_case", "build_evidence_directory", "build_visualization",
}
TOOL_STAGES = {name: list(BUSINESS_STAGES) for name in REQUEST_MODELS}
TOOL_STAGES["draft_document"] = ["litigation"]
TOOL_STAGES["build_evidence_directory"] = ["litigation"]
TOOL_STAGES["verify_output"] = BUSINESS_STAGES + ["closed"]


def _argument_models():
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


def get_case_tool_contracts() -> dict:
    """JSON-serializable ToolSpec fields, importable without Member 4's runtime."""
    contracts = {}
    for name, model in ARGUMENT_MODELS.items():
        contracts[name] = {
            "name": name,
            "owner": "member3",
            "description": DESCRIPTIONS[name],
            "parameters": model.model_json_schema(),
            "stages": list(TOOL_STAGES[name]),
            "side_effects": name in WRITING_TOOLS,
            "max_retries": 0,
        }
        if name == "verify_output":
            contracts[name]["feedback_loop"] = "retrieve_verify"
    return contracts


class CaseToolClient:
    """Shared validation and service dispatch for model and fixed-sequence calls.

    The application supplies a trusted InvocationContext and expected version.
    Only arguments may come from a model. The application's reply flow records
    user messages through RepositoryCasePort before applying confirmations.
    """

    def __init__(self, service, *, activity_sink=None):
        self.service = service
        self.activity_sink = activity_sink if activity_sink is not None else getattr(service,"activity",None)

    def invoke(self, name: str, invocation: ToolInvocation | dict) -> BusinessResult:
        result,receipt=self.invoke_recorded(name,invocation)
        if receipt.get("recording_failed"):
            warnings.warn("业务结果已返回，但调用结束记录未保存；不要据此自动重试业务操作。",RuntimeWarning,stacklevel=2)
        return result

    def invoke_recorded(self,name,invocation,*,run_id=None,step_id=None):
        """Application/runtime supplies correlation; it is not an LLM tool argument."""
        if self.activity_sink is None:
            return self._invoke(name,invocation),dict(recorded=False,reason="activity_store_not_configured")
        try:
            call=ToolInvocation.model_validate(invocation.model_dump(mode="json") if isinstance(invocation,ToolInvocation) else invocation)
            self.service.repository.get_case(call.context.case_id)
            if not isinstance(name,str) or not 0<len(name)<=200:
                raise ValueError("invalid tool name")
        except (ValueError,TypeError,KeyError):
            return self._invoke(name,invocation),dict(recorded=False,reason="invalid_or_unknown_case_context")
        try:
            start=self.activity_sink.begin(name,call,run_id=run_id,step_id=step_id)
        except Exception:
            return self._error(call.context,call.case_version,"tool_error","调用记录无法初始化，本次业务操作未执行。",{"operation_started":False}),dict(recorded=False,operation_started=False)
        started=monotonic()
        result=self._invoke(name,call)
        duration=round((monotonic()-started)*1000)
        try:
            sequence=self.activity_sink.finish(start,result,duration)
            return result,dict(recorded=True,attempt_id=start["attempt_id"],finished_sequence=sequence)
        except Exception:
            # The business write may already be committed. Never disguise it as a retryable tool failure.
            return result,dict(recorded=False,recording_failed=True,attempt_id=start["attempt_id"],operation_started=True)

    def _invoke(self, name: str, invocation: ToolInvocation | dict) -> BusinessResult:
        context = None
        version = None
        try:
            payload = invocation.model_dump(mode="json") if isinstance(invocation, ToolInvocation) else invocation
            if isinstance(payload, dict):
                context = InvocationContext.model_validate(payload.get("context"))
            call = ToolInvocation.model_validate(payload)
            context, version = call.context, call.case_version
            if name not in ARGUMENT_MODELS:
                return self._error(context, version, "not_found", "业务工具不存在。")
            if context.deadline_at and datetime.now(timezone.utc) >= context.deadline_at:
                return self._error(context, version, "timeout", "工具调用已过期。")
            arguments = ARGUMENT_MODELS[name].model_validate(call.arguments).model_dump(mode="json")
            snapshot = self.service.repository.get_case(context.case_id)
            if name != "update_case" and snapshot.case_version != version:
                raise VersionConflict(version, snapshot.case_version)
            allowed_stages = TOOL_STAGES[name]
            if snapshot.stage not in allowed_stages:
                return self._error(context, snapshot.case_version, "invalid_request", "当前案件阶段不允许此工具。",
                                   {"reason": "stage_not_allowed", "allowed_stages": allowed_stages})
            if name == "update_case":
                if arguments["expected_case_version"] != version:
                    return self._error(context, version, "invalid_request", "更新的预期版本必须等于调用版本。")
                request_fields = {"context": context, **arguments}
            elif name == "verify_output":
                output = self.service.assets.get_output(context.case_id, arguments["output_id"])["output"]
                request_fields = {
                    "context": context,
                    "case_version": snapshot.case_version,
                    "case_snapshot": snapshot,
                    "candidate_output": output,
                    "cited_sources": output.source_refs,
                }
            else:
                request_fields = {"context": context, "case_version": snapshot.case_version, **arguments}
                if "case_snapshot" in REQUEST_MODELS[name].model_fields:
                    request_fields["case_snapshot"] = snapshot
            request = REQUEST_MODELS[name].model_validate(request_fields)
            result = getattr(self.service, name)(request)
            return RESULT_MODELS[name].model_validate(result.model_dump(mode="json"))
        except VersionConflict as exc:
            return self._error(context, exc.actual, "version_conflict", "调用的案件版本已过期。",
                               {"expected_case_version": exc.expected, "actual_case_version": exc.actual})
        except CaseNotFound:
            return self._error(context, version, "not_found", "案件、文件或业务输出不存在。")
        except ValidationError as exc:
            issues = [{"location": list(item["loc"]), "type": item["type"], "message": item["msg"]}
                      for item in exc.errors(include_url=False)]
            return self._error(context, version, "invalid_request", "业务参数不合法。", {"issues": issues})
        except (ValueError, TypeError):
            return self._error(context, version, "invalid_request", "业务参数或来源适配不合法。")
        except Exception as exc:
            return self._error(context, version, "tool_error", "业务工具执行失败。",
                               {"exception_type": type(exc).__name__})

    def evaluate(self, name: str, invocation: ToolInvocation | dict) -> dict:
        """Member 5 envelope. Valid context is required for request correlation."""
        raw_context = invocation.context if isinstance(invocation, ToolInvocation) else invocation.get("context")
        context = InvocationContext.model_validate(raw_context)
        started = monotonic()
        result = self.invoke(name, invocation)
        return to_evaluation_result(result, context, duration_ms=round((monotonic() - started) * 1000))

    @staticmethod
    def _error(context, version, code, message, details=None):
        return BusinessResult(
            case_id=context.case_id if context else None,
            case_version=version,
            status="error",
            summary=message,
            data=None,
            error=BusinessError(code=code, message=message, details=details),
        )
