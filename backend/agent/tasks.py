"""Conversation tasks carried in LangGraph checkpoints as plain JSON values."""
from typing import Literal
from uuid import uuid4

from langchain.agents import AgentState
from pydantic import BaseModel, Field
from typing_extensions import NotRequired

from backend.common.schemas import CaseTask, DocumentScope, LitigationStep, TaskKind


MEDIATION_QUESTIONS = {
    "payment_history": (
        "已支付或垫付的情况",
        "此前有没有支付或垫付过这笔费用？有的话，大约多少，希望怎么计入这次调解？",
    ),
    "payment_preference": (
        "可接受的付款安排",
        "你希望怎么谈付款安排？可以说可接受的总额、一次付清还是分期，或明确表示暂不设金额、不透露预算。",
    ),
}
UNKNOWN_INPUT = {"不知道", "不清楚", "不确定", "还没想好", "还没决定", "未知", "跳过", "暂不回答", "待确认"}


class CivRAAgentState(AgentState):
    tasks: NotRequired[list[dict]]


class TaskIntent(BaseModel):
    action: Literal["new", "continue", "replace", "cancel"]
    route_step: LitigationStep | None = None
    task_id: str | None = None
    deliverable: str = Field(default="", max_length=500)
    kind: TaskKind = "general"
    formats: list[Literal["docx", "pdf", "md"]] = Field(default_factory=list, max_length=3)
    document_scope: DocumentScope | None = None
    calculation_type: str | None = Field(default=None, max_length=100)
    mediation_preliminary: bool | None = None
    review_file_ids: list[str] = Field(default_factory=list, max_length=20)
    missing: list[str] = Field(default_factory=list, max_length=8)
    review_items: list[str] = Field(default_factory=list, max_length=100)


def current(tasks):
    return next((t for t in reversed(tasks) if t.status in ("active", "waiting_input", "partial", "blocked")), None)


def select_task(tasks, intent, message):
    """Only an explicit replacement/cancellation discards an unfinished goal."""
    active = current(tasks)
    if intent is None:
        if active:
            return
        intent = TaskIntent(action="new", deliverable=message[:500])
    if (intent.action == "new" and active and intent.deliverable == active.deliverable
            and intent.kind == active.kind and intent.route_step == active.route_step):
        return
    target = next((t for t in tasks if t.task_id == intent.task_id), None) if intent.task_id else active
    if intent.action in ("cancel", "continue", "replace") and intent.task_id and target is None:
        return
    if target and target.status in ("delivered", "cancelled") and intent.action != "new":
        return
    if intent.action == "cancel":
        if target:
            target.status = "cancelled"
        return
    if intent.action == "continue" and target:
        if intent.route_step and target.route_step is None:
            target.route_step = intent.route_step
        if target.kind == "general" and intent.kind != "general":
            target.kind, target.formats = intent.kind, intent.formats
        if intent.deliverable and target.kind == intent.kind:
            target.deliverable = intent.deliverable
        if active and active is not target:
            active.status = "paused"
        target.status = "active"
        if target.kind == "document" and intent.document_scope is not None:
            target.document_scope = intent.document_scope
        if target.kind == "calculation" and intent.calculation_type is not None:
            target.calculation_type = intent.calculation_type
        if target.kind == "mediation_plan" and intent.mediation_preliminary is not None:
            target.mediation_preliminary = intent.mediation_preliminary
        if target.kind == "item_review" and intent.review_file_ids:
            target.review_file_ids = list(dict.fromkeys([*target.review_file_ids, *intent.review_file_ids]))
        for quote in intent.review_items:
            if quote.strip() and quote in message and quote not in target.review_items:
                target.review_items.append(quote)
        if "missing" in intent.model_fields_set:
            target.missing = intent.missing
            target.missing_kinds = {k:v for k,v in target.missing_kinds.items() if k in target.missing}
        return
    if intent.action == "replace" and target:
        target.status = "cancelled"
    if active and active.status != "cancelled":
        active.status = "paused"
    tasks.append(CaseTask(task_id=uuid4().hex[:12], deliverable=intent.deliverable or message[:500],
                          kind=intent.kind, route_step=intent.route_step, formats=intent.formats, missing=intent.missing,
                          document_scope=intent.document_scope or "draft", calculation_type=intent.calculation_type,
                          mediation_preliminary=bool(intent.mediation_preliminary),
                          review_file_ids=list(dict.fromkeys(intent.review_file_ids)),
                          review_items=[q for q in intent.review_items if q.strip() and q in message]))


def record_mediation_answer(task, field, text):
    value = text.strip()
    if field not in MEDIATION_QUESTIONS or not value:
        return
    if value.rstrip("。.!！") in UNKNOWN_INPUT:
        task.mediation_inputs.pop(field, None)
    else:
        task.mediation_inputs[field] = value


def mediation_gaps(task):
    return {field: question for field, question in MEDIATION_QUESTIONS.items()
            if field not in task.mediation_inputs and not task.mediation_preliminary}


def assess(task, args, messages, extracted_texts, reply_evidence, *,
           verified_missing=(), calculated=False):
    """Every finish path uses the same conservative delivery checks."""
    report = args.get("task_result") or {}
    answer = args.get("answer", "")
    missing = list(dict.fromkeys([
        *(item for item in report.get("missing", task.missing)
          if "gaps" in report or item not in task.verified_missing),
        *verified_missing,
    ]))
    task.verified_missing = list(verified_missing)
    excerpt = report.get("delivered_text", "").strip()
    delivered = bool(excerpt and excerpt in answer) or bool(
        report.get("answer_is_deliverable") and answer.strip()
        and task.kind in ("text", "general", "mediation_plan"))
    if task.kind == "unsupported_document":
        task.status = 'blocked'
        task.missing = ['当前服务不支持所请求的文书类型']
        task.missing_kinds = {task.missing[0]: "verification"}
        return False
    elif task.kind == "document":
        delivered = bool(task.output_refs)
        if not delivered:
            missing.append("尚无已核对文书类型和格式的本案文件")
    elif task.kind == "evidence_directory":
        delivered = bool(task.output_refs)
        if not delivered:
            missing.append("尚无可核对的本案证据目录成果")
    elif task.kind == "calculation":
        delivered = calculated
    elif task.kind == "mediation_plan":
        delivered = delivered or bool(answer.strip() and missing)
    elif task.kind == "item_review":
        rows = report.get("items", [])
        # Check the quoted item against a user statement or a persisted extraction.
        source_texts = [m.text for m in messages if m.type == "human"] + extracted_texts
        known_quotes = [r["quote"] for r in rows if r["quote"].strip() and
                        any(r["quote"] in text for text in source_texts)]
        # Keep observed rows separate from the user's declared review scope.
        covered = set(known_quotes)
        has_declared_scope = bool(task.review_items)
        from_material = any(quote in text for quote in covered for text in extracted_texts + reply_evidence)
        delivered = bool(covered) and (has_declared_scope or from_material) and set(task.review_items).issubset(covered)
        for row in rows:
            if not (row["quote"].strip() and any(row["quote"] in text for text in source_texts)
                    and row["quote"] in answer and row["finding"].strip() and row["finding"] in answer):
                delivered = False
        if delivered:
            task.reviewed_items = sorted(covered)
        if not delivered:
            missing.append("需要能对应原始明细的逐项核对结果")
        elif not has_declared_scope and not missing:
            missing.append("待确认上述分项是否覆盖需核对的范围")
    task.missing = list(dict.fromkeys(missing))
    task.missing_kinds = {k:v for k,v in task.missing_kinds.items() if k in task.missing}
    # Only an actual wait (with a resumable wait_id) sets waiting_input.
    task.status = "delivered" if delivered and not task.missing else "partial"
    return delivered
