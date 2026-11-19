"""Ten native LangChain case tools with model arguments and execution policy.

Each tool calls the same-named CaseService method through CaseToolClient.
Business request/response models remain in backend.case.contracts; the projection
in backend.tools.case_contracts removes application-owned fields from the model-facing arguments.
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Callable
from pydantic import ValidationError
import warnings

from backend.tools.case_results import runtime_source_match, to_runtime_tool_result
from backend.case.contracts import InvocationContext
from backend.case.calculation.cost_summary import MATERIAL_SUMMARY_VERSION
from backend.case.calculation.fee_rules import PROPERTY_FEE_RULE_VERSION
from backend.case.calculation.lending import PRINCIPAL_RULE_VERSION
from backend.case.operations import OPERATION_STAGES
from backend.tools.case_contracts import ARGUMENT_MODELS
from backend.case.models import BusinessResult, ContractModel
from backend.case.errors import CaseNotFound
from backend.common.schemas import ToolContext, ToolResult
from langchain.tools import tool, ToolRuntime
from langchain_core.tools import BaseTool
from backend.tools.policy import describe_tool, set_policy

if TYPE_CHECKING:
    from backend.tools.case_client import CaseToolClient
    from backend.tools.case_contracts import ToolInvocation
    from backend.case.service import CaseService


def _agent_update_schema():
    """Model-facing update_case arguments; the runtime supplies version, confirmer and time."""
    schema = ARGUMENT_MODELS["update_case"].model_json_schema()
    schema["required"].remove("expected_case_version")
    del schema["properties"]["expected_case_version"]
    definitions = schema["$defs"]
    confirmation = deepcopy(definitions["Confirmation"])
    confirmation["required"] = ["message_id"]
    confirmation["description"] = "引用已保存、明确确认该事实的用户消息 ID。确认人和时间由系统读取。"
    for field in ("confirmed_by", "confirmed_at"):
        del confirmation["properties"][field]
    definitions["AgentFactConfirmation"] = confirmation
    options = definitions["Fact"]["properties"]["confirmation"]["anyOf"]
    options[0]["$ref"] = "#/$defs/AgentFactConfirmation"
    return schema


def to_case_tool_result(result: BusinessResult, *, service: CaseService,
                        case_id: str, source_resolver, include_materials: bool = True) -> ToolResult:
    """Convert business output and material locations to the shared tool format."""
    converted = to_runtime_tool_result(result, source_resolver=source_resolver, result_type=ToolResult)
    material_refs = []
    if result.data is not None and include_materials:
        report_fields = ("missing_facts", "conflicts", "duplicates", "material_gaps", "supplementary_checklist")
        material_refs = [ref for field in report_fields for finding in getattr(result.data, field, [])
                         for ref in finding.material_refs]
        material_refs += [ref for entry in getattr(result.data, "entries", []) for ref in entry.material_refs]
        material_refs += [segment.material_ref for segment in getattr(result.data, "text_segments", [])]
    matches = (converted.data or {}).get("matches", [])
    seen = {match["source_ref"]["source_id"] for match in matches}
    snapshot = service.repository.get_case(case_id) if material_refs else None
    for ref in material_refs:
        source_id = (f"material:{ref.file_id}:{ref.file_version}:{ref.extraction_version}:"
                     f"{ref.page}:{ref.paragraph}:{ref.span_start}:{ref.span_end}")
        if source_id in seen:
            continue
        seen.add(source_id)
        locator = {"chunk_id": "text"}
        if ref.page is not None:
            locator["page_start"] = ref.page
        if ref.paragraph:
            locator["paragraph"] = ref.paragraph
        if ref.span_start is not None:
            locator["chunk_id"] += f":chars[{ref.span_start}:{ref.span_end}]"
        if ref.extraction_version:
            locator["chunk_id"] += ":extraction:" + ref.extraction_version
        title = next((item.file_name for item in snapshot.evidence
                      if (item.file_id, item.file_version) == (ref.file_id, ref.file_version)), None)
        if title is None:
            title = service.assets.get_file(case_id, ref.file_id, ref.file_version)[0].file_name
        matches.append(runtime_source_match({
            "source_id": source_id, "source_type": "user_material", "title": title,
            "locator": locator, "version": ref.file_version, "text": ref.quote or "",
        }))
    if material_refs:
        if converted.data is None:
            converted.data = {}
        converted.data["matches"] = matches
    return converted


def _make_invocation(name: str, args: dict, ctx: ToolContext) -> ToolInvocation:
    """Map runtime-owned IDs and deadline; model arguments cannot replace them."""
    from backend.tools.case_contracts import ToolInvocation

    return ToolInvocation(
        context=InvocationContext(
            request_id=f"{ctx.run_id}:tool:{ctx.step_id}:{name}",
            case_id=ctx.case_id, experiment_run_id=None, deadline_at=ctx.deadline,
        ),
        case_version=ctx.case_version, arguments=args,
    )


def _execute_case_tool(name: str, handler: Callable[[ContractModel], BusinessResult],
                       args: dict, ctx: ToolContext, *, client: CaseToolClient,
                       source_resolver) -> ToolResult:
    """Shared boundary: build context, retain business receipts, adapt the result.

    Scheduling, budgets, timeouts and Agent trace remain in the runtime middleware.
    The client retains its existing validation and case activity recording.
    """
    result = None
    try:
        result, receipt = client.invoke_recorded(
            name, _make_invocation(name, args, ctx),
            handler=handler, run_id=ctx.run_id, step_id=str(ctx.step_id),
        )
        if receipt.get("recording_failed"):
            warnings.warn("业务调用已完成，结束记录失败；请按run_id/step_id查询，勿自动重试写操作。", RuntimeWarning)
        return to_case_tool_result(
            result, service=client.service, case_id=ctx.case_id,
            source_resolver=source_resolver, include_materials=name != "update_case",
        )
    except CaseNotFound:
        failure = ToolResult.fail("NOT_FOUND", "案件、文件或业务输出不存在。")
        if result is not None:
            failure.case_version = result.case_version
            failure.error.details = {"output_refs": result.output_refs}
        return failure
    except (ValidationError, ValueError):
        failure = ToolResult.fail("INVALID_ARGS", "业务参数或来源适配不合法。")
        if result is not None:
            failure.case_version = result.case_version
            failure.error.details = {"output_refs": result.output_refs}
        return failure
    except Exception as exc:
        return ToolResult(
            status="error", data=None, summary="业务结果适配失败，可按输出引用查询已保存结果。",
            case_version=result.case_version if result else None,
            error={
                "code": "INTERNAL", "message": "业务结果适配失败。", "retryable": False,
                "details": {"exception_type": type(exc).__name__,
                            "output_refs": result.output_refs if result else []},
            },
        )


def create_case_tools(service: CaseService, *, source_resolver=None) -> list[BaseTool]:
    """Ten explicit native tools, with one shared business adapter."""
    from backend.tools.case_client import CaseToolClient

    client = CaseToolClient(service) if service is not None else None
    resolver = source_resolver or (service.get_source if service is not None else None)

    def execute(name, handler, args, runtime):
        return _execute_case_tool(name, handler, args, runtime.context,
                                  client=client, source_resolver=resolver).model_dump(
                                      mode="json", exclude_none=True)

    @tool("parse_documents", description='读取本案已上传文件，提取文字及候选事实，返回原文位置和 OCR 警告。',
          args_schema=ARGUMENT_MODELS["parse_documents"].model_json_schema())
    def parse_documents(runtime: ToolRuntime[ToolContext], **args) -> dict:
        return execute("parse_documents", service.parse_documents, args, runtime)

    @tool("check_evidence", description='检查缺失事实、冲突、相同文件及诉求材料缺口，返回位置与建议问题。',
          args_schema=ARGUMENT_MODELS["check_evidence"].model_json_schema())
    def check_evidence(runtime: ToolRuntime[ToolContext], **args) -> dict:
        return execute("check_evidence", service.check_evidence, args, runtime)

    @tool("calculate_costs", description=(
        "按明确输入执行计算。仅用已注册的计算类型与规则版本。"
        "通用算术用 arithmetic_v1。金额对象包含非负十进制字符串 amount 和 currency。"
        "sum_amounts 的 inputs 为 amounts 金额列表；subtract_amounts 为 base 金额和非空 deductions 金额列表；"
        "multiply 为 amount 金额和 multiplier。不要把未知扣减当零。"
        f"principal_balance 使用 {PRINCIPAL_RULE_VERSION}；"
        f"materials_total、materials_breakdown 使用 {MATERIAL_SUMMARY_VERSION}；"
        f"property_case_acceptance_fee 使用 {PROPERTY_FEE_RULE_VERSION}。"
        "乘数必须有明确依据，工具不推定赔偿分担比例。规则不支持时说明局限，继续当前交付。"),
          args_schema=ARGUMENT_MODELS["calculate_costs"].model_json_schema())
    def calculate_costs(runtime: ToolRuntime[ToolContext], **args) -> dict:
        return execute("calculate_costs", service.calculate_costs, args, runtime)

    @tool("compare_routes", description='比较协商、调解和诉讼的条件、步骤、成本及未知项，供用户选择。',
          args_schema=ARGUMENT_MODELS["compare_routes"].model_json_schema())
    def compare_routes(runtime: ToolRuntime[ToolContext], **args) -> dict:
        return execute("compare_routes", service.compare_routes, args, runtime)

    @tool("draft_document", description='诉讼阶段生成 CivRA 起诉状草稿。使用 find_form_template 返回的 draft_document_args 中的模板 ID 和版本，引用已确认事实并保留缺项。若候选无此参数，可提供官方空白原表或撰写聊天正文。',
          args_schema=ARGUMENT_MODELS["draft_document"].model_json_schema())
    def draft_document(runtime: ToolRuntime[ToolContext], **args) -> dict:
        return execute("draft_document", service.draft_document, args, runtime)

    @tool("verify_output", description='核验已保存输出的版本、字段、事实证据链接、计算和来源，列明未执行的语义检查。',
          args_schema=ARGUMENT_MODELS["verify_output"].model_json_schema())
    def verify_output(runtime: ToolRuntime[ToolContext], **args) -> dict:
        return execute("verify_output", service.verify_output, args, runtime)

    @tool("update_case", description=(
        "需要正式入档时保存事实、证据和诉求。用户原话已经由系统存档。"
        "仅转述案情时用 unconfirmed；明确确认某事实时填 confirmation.message_id 引用对应用户消息。"
        "系统提供案件版本、确认人和时间。暂不能保存时，仍可依据已有陈述继续准备方案。"),
          args_schema=_agent_update_schema())
    def update_case(runtime: ToolRuntime[ToolContext], **args) -> dict:
        args = deepcopy(args)
        ctx = runtime.context
        changes = args.get("changes", {})
        facts = changes.get("upsert_facts", [])
        if any(fact.get("confirmation") for fact in facts):
            try:
                snapshot = service.repository.get_case(ctx.case_id)
            except CaseNotFound:
                return ToolResult.fail("NOT_FOUND", "案件不存在或不可读取。").model_dump(mode="json")
            statements = {statement.message_id: statement for statement in snapshot.statements}
            for fact in facts:
                confirmation = fact.get("confirmation")
                if not confirmation:
                    continue
                statement = statements.get(confirmation["message_id"])
                if statement is None:
                    return ToolResult.fail(
                        "INVALID_ARGS", "confirmation.message_id 必须引用本案已保存的用户消息。"
                    ).model_dump(mode="json")
                confirmation["confirmed_by"] = statement.actor_id
                confirmation["confirmed_at"] = statement.created_at.isoformat()
        args["expected_case_version"] = ctx.case_version
        return execute("update_case", service.update_case, args, runtime)

    @tool("build_evidence_directory", description='在诉讼阶段生成证据目录、拟证明事项和补证清单，保留冲突与未确认提示。',
          args_schema=ARGUMENT_MODELS["build_evidence_directory"].model_json_schema())
    def build_evidence_directory(runtime: ToolRuntime[ToolContext], **args) -> dict:
        return execute("build_evidence_directory", service.build_evidence_directory, args, runtime)

    @tool("get_route_workflow", description='仅在用户明确需要结构化办理清单或进度计划时，根据已选路线生成条件化步骤。选择路线本身不要求调用；不能用清单替代当前实际办理成果。',
          args_schema=ARGUMENT_MODELS["get_route_workflow"].model_json_schema())
    def get_route_workflow(runtime: ToolRuntime[ToolContext], **args) -> dict:
        return execute("get_route_workflow", service.get_route_workflow, args, runtime)

    @tool("build_visualization", description='返回当前案件时间线或证据关系数据，保留来源、未确认及已取代状态。',
          args_schema=ARGUMENT_MODELS["build_visualization"].model_json_schema())
    def build_visualization(runtime: ToolRuntime[ToolContext], **args) -> dict:
        return execute("build_visualization", service.build_visualization, args, runtime)

    tools = [
        parse_documents, check_evidence, calculate_costs, compare_routes, draft_document,
        verify_output, update_case, build_evidence_directory, get_route_workflow, build_visualization,
    ]
    # Shared case policy: no automatic retries; only these two tools are read-only.
    read_only = {"check_evidence", "calculate_costs"}
    for native in tools:
        set_policy(native, owner="member3", max_retries=0,
                   stages=list(OPERATION_STAGES[native.name]),
                   side_effects=native.name not in read_only)
    return tools


# Business callers supply full request fields even where the Agent tool fills them in.
CASE_TOOL_SPECS = {t.name: describe_tool(t) for t in create_case_tools(None)}


def get_case_tool_contracts() -> dict:
    fields = {"name", "owner", "description", "parameters", "stages", "side_effects", "max_retries"}
    return {name: {**spec.model_dump(include=fields),
                   "parameters": ARGUMENT_MODELS[name].model_json_schema()}
            for name, spec in CASE_TOOL_SPECS.items()}
