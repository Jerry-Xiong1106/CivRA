"""Ten native LangChain case tools with model arguments and execution policy.

Each tool calls the same-named CaseService method through CaseToolClient.
Business request/response models remain in backend.case.contracts; the projection
in backend.tools.case_contracts removes application-owned fields from the model-facing arguments.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable
from pydantic import ValidationError
import warnings

from backend.tools.case_results import runtime_source_match, to_runtime_tool_result
from backend.case.contracts import InvocationContext
from backend.tools.case_contracts import ARGUMENT_MODELS, OPERATION_STAGES
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

    @tool("calculate_costs", description='按明确输入和版本化规则执行 Decimal 计算，返回公式、依据与未确定参数。',
          args_schema=ARGUMENT_MODELS["calculate_costs"].model_json_schema())
    def calculate_costs(runtime: ToolRuntime[ToolContext], **args) -> dict:
        return execute("calculate_costs", service.calculate_costs, args, runtime)

    @tool("compare_routes", description='比较协商、调解和诉讼的条件、步骤、成本及未知项，供用户选择。',
          args_schema=ARGUMENT_MODELS["compare_routes"].model_json_schema())
    def compare_routes(runtime: ToolRuntime[ToolContext], **args) -> dict:
        return execute("compare_routes", service.compare_routes, args, runtime)

    @tool("draft_document", description='在用户选择诉讼后按版本化模板生成草稿，使用选定的已确认事实并保留缺失项。',
          args_schema=ARGUMENT_MODELS["draft_document"].model_json_schema())
    def draft_document(runtime: ToolRuntime[ToolContext], **args) -> dict:
        return execute("draft_document", service.draft_document, args, runtime)

    @tool("verify_output", description='核验已保存输出的版本、字段、事实证据链接、计算和来源，列明未执行的语义检查。',
          args_schema=ARGUMENT_MODELS["verify_output"].model_json_schema())
    def verify_output(runtime: ToolRuntime[ToolContext], **args) -> dict:
        return execute("verify_output", service.verify_output, args, runtime)

    @tool("update_case", description='按预期版本保存事实、证据和诉求变更，确认操作须引用应用已保存的用户消息。',
          args_schema=ARGUMENT_MODELS["update_case"].model_json_schema())
    def update_case(runtime: ToolRuntime[ToolContext], **args) -> dict:
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
                   side_effects=native.name not in read_only,
                   feedback_loop="retrieve_verify" if native.name == "verify_output" else None)
    return tools


# Compatibility views for business-contract export; never used to construct Agent tools.
CASE_TOOL_SPECS = {t.name: describe_tool(t) for t in create_case_tools(None)}


def get_case_tool_contracts() -> dict:
    fields = {"name", "owner", "description", "parameters", "stages", "side_effects", "max_retries"}
    return {name: spec.model_dump(include=fields | ({"feedback_loop"} if spec.feedback_loop else set()))
            for name, spec in CASE_TOOL_SPECS.items()}
