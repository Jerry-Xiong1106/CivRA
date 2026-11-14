"""User-facing case tasks and append-only progress in the existing artifact store."""

from pydantic import Field, ValidationError, model_validator

from .business_support import case_basis, deadline, digest, failure, review_sources, unique
from .contracts import CaseRequest, RouteWorkflowData, RouteWorkflowRequest, WorkflowStep
from .evidence import check_evidence
from .models import BusinessError, BusinessOutput, BusinessResult, ContractModel, Identifier, Route
from .repository import CaseNotFound, VersionConflict
from .research import SourceSelection
from .route_comparison import _case_context
from .workflow_models import (
    TaskStatus, WORKFLOW_VERSION, WorkflowEdge, WorkflowProgressRecord, WorkflowSourceCheck,
)


class WorkflowProgressRequest(CaseRequest):
    route: Route


class WorkflowProgressData(ContractModel):
    route: Route
    revision: int
    records: dict[str, WorkflowProgressRecord]


class WorkflowProgressChange(ContractModel):
    step_id: Identifier
    status: TaskStatus
    note: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[Identifier] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def meaningful_note(self):
        if not self.note.strip() or len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("progress note must be nonblank; evidence IDs must be unique")
        return self


class RecordWorkflowProgressRequest(WorkflowProgressRequest):
    expected_progress_version: int = Field(ge=0, strict=True)
    plan_output_id: Identifier
    message_id: Identifier
    actor_id: Identifier
    quote: str = Field(min_length=1, max_length=4000)
    changes: list[WorkflowProgressChange] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def unique_steps(self):
        if len({item.step_id for item in self.changes}) != len(self.changes):
            raise ValueError("a step may be changed only once per request")
        if not self.quote.strip():
            raise ValueError("quote must be nonblank")
        return self


def _optional_steps(snapshot):
    steps = []
    if snapshot.dispute_type == "traffic_accident" or any(fact.field == "insurance" for fact in snapshot.facts):
        steps.append(WorkflowStep(step_id="insurance_review", title="核对保险关系、理赔材料与实际处理结果",
            contact=None, prerequisites=["先核实保险关系、可联系主体和具体办理要求"],
            materials=["保险资料（如有）", "事故与损失材料", "理赔通知及实际付款记录（如有）"],
            completion_condition="记录实际理赔进展、已履行项目和仍有争议的事项",
            next_if_unsolved=["核实尚缺材料或争议原因；由用户选择后续路径，不自动转入诉讼"],
            source_refs=[], conditional=True, applicability="仅供涉及保险的事项核对；未认定存在保险责任或必须先理赔"))
    steps.extend([
        WorkflowStep(step_id="performance_review", title="记录协议或结果的实际履行情况", contact=None,
            prerequisites=["先核对实际协议、结果文书及其条件"], materials=["协议或结果文书", "付款及其他履行凭证（如有）"],
            completion_condition="逐项记录已履行、部分履行、未履行或有争议的实际情况",
            next_if_unsolved=["核对未解决事项及可用程序；不因勾选待办就自动结案"], source_refs=[], conditional=True,
            applicability="取得实际协议或处理结果后按需核对；不预设已经取得结果"),
        WorkflowStep(step_id="enforcement_review", title="核验是否具备申请执行条件", contact=None,
            prerequisites=["核对可执行依据及效力、履行情况、申请主体、管辖和期间等条件"],
            materials=["可能的执行依据及效力资料", "履行情况资料", "具体渠道要求的其他资料（待核实）"],
            completion_condition="记录条件核验及实际办理反馈；完成本项不等于法院已经受理执行",
            next_if_unsolved=["条件不明时补充核验；已履行事项不重复申请，具体范围仍需核实"], source_refs=[], conditional=True,
            applicability="仅为条件核验节点；未认定协议当然可执行，不自动启动申请执行"),
    ])
    return steps


def plan_basis(request):
    return digest(dict(version=WORKFLOW_VERSION, case_basis=case_basis(request.case_snapshot), route=request.route,
        source_notes=[item.model_dump(mode="json") for item in request.source_notes],
        dates=[item.model_dump(mode="json") for item in request.dates]))


def build_workflow(payload, *, progress=None, progress_is_latest=True, evidence_checker=None, source_reviewer=None):
    case_id, version = None, None
    try:
        request = RouteWorkflowRequest.model_validate(payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload)
        snapshot = request.case_snapshot
        case_id, version = snapshot.case_id, snapshot.case_version
        deadline(request)
        if snapshot.selected_route != request.route or snapshot.stage != request.route:
            raise ValueError("workflow requires matching user-selected route")
        if progress is None:
            if request.progress_version not in (None, 0):
                raise ValueError("explicit progress version needs the persisted progress reader")
            progress = WorkflowProgressData(route=request.route, revision=0, records={})
        if progress.route != request.route or request.progress_version not in (None, progress.revision):
            raise ValueError("workflow progress route/version mismatch")
        report = (evidence_checker or check_evidence)({"context": request.context, "case_version": version, "case_snapshot": snapshot})
        if report.status == "error":
            return BusinessResult(case_id=case_id, case_version=version, status="error", data=None,
                summary="证据检查未完成，未生成待办。", error=report.error)
        context = _case_context(snapshot, report.data)
        from .routes import TEMPLATES
        steps = [WorkflowStep(step_id=item[0], title=item[1], contact=item[2], prerequisites=list(item[3]),
            materials=list(item[4]), completion_condition=item[5], next_if_unsolved=list(item[6]), source_refs=[])
            for item in TEMPLATES[request.route]]
        optional = _optional_steps(snapshot)
        by_step = {item.step_id: item for item in steps + optional}
        if any(item.step_id not in by_step for item in request.source_notes + request.dates):
            raise ValueError("annotation refers to a step outside this workflow")
        unresolved = ["所在地办理机构、具体入口、适用条件和期限起算需要按案件核实。",
                      "尚未根据原文核实的程序内容不得作为确定法律意见。"]
        if not request.available_authorities:
            unresolved.append("当前未提供可追溯的法律或程序依据。")
        for pointer in request.available_authorities:
            if not any(item.source_ref == pointer for item in request.source_notes):
                unresolved.append(f"{pointer.source_id}: 来源尚未绑定到具体步骤及原文片段。")
        selections = [SourceSelection(source_ref=item.source_ref, expected_kind="statute", quote=item.quote) for item in request.source_notes]
        cards, issues = review_sources(request, selections, source_reviewer)
        unresolved.extend(issues)
        checks = []
        for note in request.source_notes:
            card = cards.get((note.source_ref.source_id, note.source_ref.source_version, "statute", note.quote))
            reason = "只核对原文定位；办理对象、材料要求、适用条件和期限含义仍需语义及法律适用核验。" if card else "该步骤来源未通过原文定位核对。"
            checks.append(WorkflowSourceCheck(note_id=note.note_id, step_id=note.step_id, aspect=note.aspect,
                proposed_note=note.note, source=card.model_dump() if card else None,
                status="original_text_checked" if card else "rejected", reason=reason))
            unresolved.append(f"{note.step_id}/{note.note_id}: {reason}")
            if card:
                by_step[note.step_id].source_note_ids.append(note.note_id)
                by_step[note.step_id].source_refs = unique(by_step[note.step_id].source_refs + [card.source_ref])
                unresolved.extend(card.warnings)
        facts = {item.fact_id: item for item in snapshot.facts}
        for date_input in request.dates:
            fact = facts.get(date_input.fact_id)
            provenance = "user_statement" if date_input.kind == "user_target" else "document_record"
            if fact is None or fact.fact_id not in context.confirmed_fact_ids or fact.value_type != "date" or fact.provenance != provenance:
                unresolved.append(f"{date_input.step_id}: 日期需要有效、无冲突、已确认且来源类型相符的日期事实，保持未知。")
                continue
            step = by_step[date_input.step_id]
            step.deadline, step.deadline_kind, step.deadline_fact_id = fact.value, date_input.kind, fact.fact_id
            unresolved.append(f"{step.step_id}: 此日期是{'用户目标' if date_input.kind == 'user_target' else '材料记载'}，不是程序计算或已核验的法定截止日。")
        basis = plan_basis(request)
        for step in steps + optional:
            step.finding_ids = [item.finding_id for item in context.findings]
            record = progress.records.get(step.step_id)
            if record:
                step.progress = record
                needs_review = record.plan_basis_hash != basis or not progress_is_latest
                step.status = "needs_review" if needs_review else record.status
                step.should_skip = not needs_review and record.status in {"completed", "not_applicable"}
                if needs_review:
                    unresolved.append(f"{step.step_id}: 原进度保留，但案情/计划依据或进度版本变化，需要重新核对。")
                if record.status == "blocked":
                    unresolved.append(f"{step.step_id}: 用户记录受阻，原因：{record.note}")
        unresolved.extend(f"证据问题{item.finding_id}: {item.message}" for item in context.findings)
        if context.pending_fact_ids:
            unresolved.append("待核对事实：" + "、".join(context.pending_fact_ids))
        edges = [WorkflowEdge(from_step=left.step_id, to_step=right.step_id, kind="suggested_order",
                              condition="整理顺序提示，可按实际进展调整，不是法定必经顺序")
                 for left, right in zip(steps, steps[1:])]
        edges.append(WorkflowEdge(from_step=steps[-1].step_id, to_step="performance_review", kind="conditional",
                                  condition="实际取得协议或处理结果后按需核对履行"))
        edges.append(WorkflowEdge(from_step="performance_review", to_step="enforcement_review", kind="conditional",
                                  condition="仅对未解决事项先核验执行依据和全部适用条件"))
        deadline(request)
        data = RouteWorkflowData(route=request.route, template_version=WORKFLOW_VERSION, steps=steps,
            conditional_steps=optional, edges=edges, findings=context.findings, source_checks=checks,
            progress_version=progress.revision, progress_is_latest=progress_is_latest,
            case_basis_hash=case_basis(snapshot), plan_basis_hash=basis,
            pending_step_ids=[item.step_id for item in steps + optional if not item.should_skip], unresolved_items=unique(unresolved),
            limitations=context.limitations + [
                "进度由已保存用户反馈的结构化操作记录；完成某项核对不等于法律条件成立、款项收齐或案件结案。",
                "已完成/用户标记不适用的节点仍保留在图中，仅在依据未变时可从待办跳过。",
                "conditional_steps为可选条件核验节点，不是追加的必经程序；不发送申请、不自动切换路线。",
                "本模块不自动计算法律期间，也不从原文片段自动提取机构地址或网址。",
            ])
        return BusinessResult[RouteWorkflowData](case_id=case_id, case_version=version, status="insufficient",
            summary="已生成所选路线的条件化待办草稿。", data=data,
            source_refs=unique([ref for item in steps + optional for ref in item.source_refs]))
    except (ValidationError, ValueError, KeyError, TimeoutError) as exc:
        return failure(case_id, version, exc)


class CaseWorkflowService:
    """Storage adapter uses immutable workflow outputs; no new DB engine or case fields."""

    def __init__(self, repository, assets, *, evidence_checker=None, source_reviewer=None):
        self.repository, self.assets = repository, assets
        self.evidence_checker, self.source_reviewer = evidence_checker, source_reviewer

    @staticmethod
    def _output_id(case_id, route):
        return "workflow_progress_" + digest([case_id, route])[:24]

    def _read(self, case_id, route, version=None):
        if version == 0:
            return WorkflowProgressData(route=route, revision=0, records={}), {}, None
        try:
            output = self.assets.get_output(case_id, self._output_id(case_id, route), version)["output"]
        except CaseNotFound:
            if version is not None:
                raise
            return WorkflowProgressData(route=route, revision=0, records={}), {}, None
        payload = output.structured_data
        if output.output_type != "workflow" or payload.get("_kind") != "user_workflow_progress_v1":
            raise ValueError("unexpected stored workflow progress type")
        data = WorkflowProgressData.model_validate(payload["_business_result"]["data"])
        if data.route != route or data.revision != output.output_version:
            raise ValueError("stored progress identity mismatch")
        return data, payload["_request_index"], output

    def resolve_version(self, payload):
        request = RouteWorkflowRequest.model_validate(payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload)
        deadline(request)
        data, _, _ = self._read(request.context.case_id, request.route)
        if request.progress_version is None:
            request = request.model_copy(update={"progress_version": data.revision})
        elif request.progress_version != data.revision:
            raise ValueError("requested workflow progress version is no longer current; refresh before planning")
        return request

    def build(self, request):
        try:
            progress, _, _ = self._read(request.context.case_id, request.route, request.progress_version)
            latest, _, _ = self._read(request.context.case_id, request.route)
            return build_workflow(request, progress=progress, progress_is_latest=progress.revision == latest.revision,
                                  evidence_checker=self.evidence_checker, source_reviewer=self.source_reviewer)
        except (ValueError, KeyError, TimeoutError) as exc:
            return failure(request.context.case_id, request.case_version, exc)

    def get_progress(self, payload):
        case_id, version = None, None
        try:
            request = WorkflowProgressRequest.model_validate(payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload)
            case_id, version = request.context.case_id, request.case_version
            deadline(request)
            snapshot = self.repository.get_case(case_id)
            if snapshot.case_version != version:
                raise VersionConflict(version, snapshot.case_version)
            data, _, output = self._read(case_id, request.route)
            deadline(request)
            return BusinessResult[WorkflowProgressData](case_id=case_id, case_version=version, status="ok",
                summary="已读取该路线的最新用户进度；有效性请结合当前计划核对。", data=data,
                output_refs=[output.output_id] if output else [])
        except (ValidationError, ValueError, KeyError, TimeoutError) as exc:
            return failure(case_id, version, exc)

    def record_progress(self, payload):
        case_id, version = None, None
        try:
            request = RecordWorkflowProgressRequest.model_validate(payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload)
            case_id, version = request.context.case_id, request.case_version
            deadline(request)
            snapshot = self.repository.get_case(case_id)
            head, index, _ = self._read(case_id, request.route)
            request_hash = digest(request)
            retry = index.get(request.context.request_id)
            if retry is not None:
                if retry["digest"] != request_hash:
                    raise ValueError("request_id was reused with different progress arguments")
                _, _, old = self._read(case_id, request.route, retry["revision"])
                return BusinessResult[WorkflowProgressData].model_validate(old.structured_data["_business_result"])
            if snapshot.case_version != version:
                raise VersionConflict(version, snapshot.case_version)
            if snapshot.selected_route != request.route or snapshot.stage != request.route:
                raise ValueError("progress requires matching user-selected route")
            if head.revision != request.expected_progress_version:
                return self._revision_conflict(case_id, version, request.expected_progress_version, head.revision)
            if len(index) >= 1000:
                raise ValueError("workflow progress history limit reached; archive support is required")
            output = self.assets.get_output(case_id, request.plan_output_id)["output"]
            if output.output_type != "workflow" or not request.plan_output_id.startswith("get_route_workflow_"):
                raise ValueError("progress requires a saved workflow plan")
            plan = RouteWorkflowData.model_validate(output.structured_data["_business_result"]["data"])
            if plan.template_version != WORKFLOW_VERSION or plan.route != request.route or plan.case_basis_hash != case_basis(snapshot):
                raise ValueError("saved workflow plan no longer matches current case; regenerate it")
            if plan.progress_version != head.revision or not plan.progress_is_latest:
                raise ValueError("saved workflow plan has stale progress; regenerate it")
            statements = {item.message_id: item for item in snapshot.statements}
            statement = statements.get(request.message_id)
            if statement is None or statement.actor_id != request.actor_id or request.quote not in statement.text:
                raise ValueError("progress must reference the actual saved user's statement and a literal quote")
            steps = {item.step_id for item in plan.steps + plan.conditional_steps}
            evidence = {item.evidence_id for item in snapshot.evidence if item.status != "excluded"}
            records = {key: value.model_copy(deep=True) for key, value in head.records.items()}
            for change in request.changes:
                if change.step_id not in steps or set(change.evidence_ids) - evidence:
                    raise ValueError("progress references unknown steps or unavailable evidence")
                records[change.step_id] = WorkflowProgressRecord(step_id=change.step_id, status=change.status,
                    note=change.note, message_id=request.message_id, actor_id=request.actor_id, quote=request.quote,
                    evidence_ids=change.evidence_ids, plan_output_id=output.output_id, plan_basis_hash=plan.plan_basis_hash,
                    case_version=version, progress_version=head.revision + 1)
            data = WorkflowProgressData(route=request.route, revision=head.revision + 1, records=records)
            result = BusinessResult[WorkflowProgressData](case_id=case_id, case_version=version, status="ok", data=data,
                summary="已记录用户待办反馈，未改变案件事实、路线或结案状态。", output_refs=[self._output_id(case_id, request.route)])
            updated_index = {**index, request.context.request_id: {"digest": request_hash, "revision": data.revision}}
            saved = BusinessOutput(output_id=result.output_refs[0], output_version=data.revision, case_id=case_id,
                case_version=version, output_type="workflow", text=result.summary, review_status="needs_review",
                structured_data={"_kind": "user_workflow_progress_v1", "_request_index": updated_index,
                                 "_business_result": result.model_dump(mode="json")})
            deadline(request)
            try:
                self.assets.save_output(saved, deadline_at=request.context.deadline_at)
            except ValueError:
                current, _, _ = self._read(case_id, request.route)
                if current.revision != head.revision:
                    return self._revision_conflict(case_id, version, head.revision, current.revision)
                raise
            return result
        except (ValidationError, ValueError, KeyError, TimeoutError) as exc:
            return failure(case_id, version, exc)

    @staticmethod
    def _revision_conflict(case_id, version, expected, actual):
        return BusinessResult(case_id=case_id, case_version=version, status="error", data=None,
            summary="待办进度已更新，请重新读取后提交。", error=BusinessError(code="version_conflict",
                message="workflow progress revision conflict", details={"expected_progress_version": expected, "actual_progress_version": actual}))
