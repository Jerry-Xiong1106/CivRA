"""Read-only route view and application-confirmed progress over the existing workflow service."""
import re

from backend.case.planning.contracts import RouteWorkflowRequest
from backend.case.business_support import case_basis
from backend.case.planning.routes import TEMPLATES
from backend.case.planning.workflow import plan_basis
from backend.case.planning.workflow_models import LitigationFeedbackEvent as Feedback
from backend.agent.tasks import TaskIntent, current, select_task


# The user selects an event; a model's answer or an exported document cannot create one.
EVENTS = {
    "intake_checked": ("jurisdiction", "completed", "已核对起诉准备事项"),
    "evidence_reviewed": ("evidence", "completed", "已复核证据目录"),
    "draft_reviewed": ("draft", "completed", "已复核起诉状"),
    "submitted": ("submit", "in_progress", "已提交，等待法院反馈"),
    "accepted": ("submit", "completed", "法院已受理"),
    "correct_evidence": ("evidence", "in_progress", "收到证据材料补正要求"),
    "correct_draft": ("draft", "in_progress", "收到文书补正要求"),
}

ENTRY_TEXT = "我选择诉讼路线，请根据本案已经做到的步骤引导我继续准备。"


def entry_action(stage, previous, switch_wait_id=None):
    if stage == "closed" or (previous and previous.status == "running"):
        raise ValueError("案件已关闭或运行中，无法开始新的引导。")
    wait = previous.wait if previous else None
    if switch_wait_id and (not wait or wait.wait_id != switch_wait_id):
        raise ValueError("原等待项已失效。")
    if stage == "litigation" and previous:
        active = current(previous.tasks)
        related = active and active.route_step
        finished_here = any(t.route_step and t.delivery_run_id == previous.run_id for t in previous.tasks)
        if (wait and related) or (not wait and previous.status == "completed" and (related or finished_here)):
            return "reuse"
    if not wait:
        return "start"
    if wait.kind == "route_selection" and any(o.route == "litigation" for o in wait.options):
        return "resume"
    if not switch_wait_id:
        raise ValueError("原路线有待答追问；请先回答，或用当前 switch_wait_id 明确切换。")
    return "switch"


def confirmed_feedback(wait, reply):
    for question in wait.questions:
        field = question.field or ""
        event = field.removeprefix("litigation_feedback.")
        answer = reply.answers.get(question.question_id) or (reply.text if len(wait.questions) == 1 else "")
        if field.startswith("litigation_feedback.") and event in EVENTS and answer and answer.strip() == "确认":
            return event
    return None


def route_task(tasks, progress):
    step = progress["current_step"]
    if not step:
        select_task(tasks, TaskIntent(action="new", kind="general", route_step="submit",
                                      deliverable="记录诉讼办理反馈"), "")
        return
    old = next((t for t in reversed(tasks) if t.route_step == step and
                t.status not in ("delivered", "cancelled")), None)
    if old:
        select_task(tasks, TaskIntent(action="continue", task_id=old.task_id), "")
        return
    kind = "general"
    if step == "evidence" and progress["role"] != "defendant" and not progress["artifacts"]["evidence"]:
        kind = "evidence_directory"
    if step == "draft" and progress["role"] == "plaintiff" and not progress["artifacts"]["draft"]:
        kind = "document"
    intent = TaskIntent(action="new", deliverable=progress["next_action"], route_step=step, kind=kind)
    select_task(tasks, intent, intent.deliverable)


class LitigationGuide:
    def __init__(self, service):
        self.service = service

    @staticmethod
    def _request(case, key):
        return dict(context=dict(case_id=case.case_id, request_id=key, deadline_at=None,
                                 experiment_run_id=None), case_version=case.case_version, route="litigation")

    def view(self, case, tasks=(), wait=None, inventory=None):
        if case.selected_route != "litigation":
            return None
        request = self._request(case, "litigation_view")
        progress = self.service.workflow.latest_progress(case.case_id, "litigation")
        basis = plan_basis(RouteWorkflowRequest(**request, case_snapshot=case))
        inventory = inventory or self.service.assets.list_case_inventory(case.case_id)
        outputs = {"evidence": [], "draft": []}
        artifact_gaps = {}
        populated_directories = set()
        for o in sorted(inventory["outputs"], key=lambda o: (o.case_version, o.output_version), reverse=True):
            if o.output_type == "evidence_directory":
                outputs["evidence"].append(o.output_id)
                artifact_gaps[o.output_id] = [f["message"] for f in (o.structured_data or {}).get("supplementary_checklist", [])]
                if (o.structured_data or {}).get("entries"):
                    populated_directories.add(o.output_id)
            elif o.output_type == "document" and (o.template_id or "").startswith("cn_complaint_"):
                outputs["draft"].append(o.output_id)
                artifact_gaps[o.output_id] = o.missing_fields
        steps = []
        for step_id, title, _, _, _, condition, _ in TEMPLATES["litigation"]:
            record = progress.records.get(step_id)
            status = record.status if record else "pending"
            if record and record.plan_basis_hash != basis and step_id != "submit":
                status = "needs_review"
            if status == "pending" and outputs.get(step_id):
                status = "needs_review"
            steps.append(dict(step_id=step_id, title=title, completion_condition=condition, status=status,
                              feedback=record.note if record else None, event=record.event if record else None,
                              output_refs=outputs.get(step_id, [])))
        pending = [s for s in steps if s["status"] not in ("completed", "not_applicable")]
        statuses = {s["step_id"]: s["status"] for s in steps}
        role = None
        for statement in case.statements:
            # Only direct self-identification; a question, quoted pleading or
            # mere mention of the opposing party cannot establish the user's role.
            for line in statement.text.splitlines():
                match = re.match(r"^(?:答：)?(?:我是|我方是|我作为)(原告|被告)(?:[，。；、\s]|$)", line.strip())
                if match:
                    role = "defendant" if match[1] == "被告" else "plaintiff"
        current = pending[0]["step_id"] if pending else None
        action = {
            "jurisdiction": "核对用户角色、诉求和已知程序信息，明确下一项准备工作",
            "evidence": "读取本案已有材料，将诉求与证据对应，准备证据目录并复核缺项",
            "draft": "根据已有材料起草或修改起诉状，保留待补项并请用户复核",
            "submit": "核对材料及提交渠道，等待用户报告实际办理结果",
        }.get(current, "本阶段已完成，可查看办理记录")
        submission = progress.records.get("submit")
        if submission and submission.event in ("accepted", "submitted"):
            current = None if submission.event == "accepted" else "submit"
            action = "已记录法院受理反馈" if current is None else "已记录实际提交，等待受理或补正反馈"
        elif submission and submission.event in ("correct_draft", "correct_evidence"):
            target = EVENTS[submission.event][0]
            revised = progress.records.get(target)
            ready = revised and revised.progress_version > submission.progress_version and statuses[target] == "completed"
            current = "submit" if ready else target
            action = "补正内容已复核，请核对重新提交情况" if ready else submission.note
        elif submission and submission.status in ("completed", "in_progress"):
            current, action = "submit", "已有提交步骤的历史记录，请核对具体是已提交、已受理还是等待补正"
        elif statuses["draft"] == "completed":
            current, action = "submit", "已复核起诉状，请核对材料包和提交渠道，并记录实际提交结果"
        elif outputs["draft"] and any(s["step_id"] == "draft" for s in pending):
            current, action = "draft", "已有起诉状，先复核现有版本、缺项和修改请求"
        elif (statuses["evidence"] == "completed" or
              (statuses["evidence"] == "in_progress" and populated_directories
               and progress.records["evidence"].event == "evidence_reviewed")):
            current, action = "draft", "证据目录已复核，根据诉求准备文书并核对待补字段"
        elif outputs["evidence"] and any(s["step_id"] == "evidence" for s in pending):
            current, action = "evidence", "已有证据目录，先核对未解决项目和对应材料"
        elif current == "jurisdiction" and case.claims and (case.evidence or inventory["files"]):
            current, action = "evidence", "已有诉求及材料，先核对对应关系和缺项"
        if role == "defendant" and (current in ("jurisdiction", "draft") or
                                    current == "evidence" and not submission):
            current, action = "evidence", "先读取收到的诉讼材料，逐项整理对方诉求、回应和相应证据；答辩状导出尚未支持"
        elif current == "jurisdiction" and role == "plaintiff":
            action = "核对你希望向谁主张什么，以及现有材料能支持哪些诉求"
        linked = next((t for t in reversed(tasks) if t.route_step == current and current
                       and t.status != "cancelled"), None)
        gaps = list(linked.missing) if linked else []
        if outputs.get(current):
            gaps.extend(artifact_gaps.get(outputs[current][0], []))
        return dict(route="litigation", role=role, current_step=current, next_action=action, steps=steps,
                    artifacts=outputs, artifact_gaps=artifact_gaps,
                    progress_version=progress.revision, task_id=linked.task_id if linked else None,
                    task_status=linked.status if linked else None,
                    missing=list(dict.fromkeys(gaps)), wait_id=wait.wait_id if wait else None,
                    files=[f.file_id for f in inventory["files"]], inventory_truncated=inventory["truncated"])

    def validate_feedback(self, case, event, text=""):
        view = self.view(case)
        if not view:
            raise ValueError("请先进入诉讼路线")
        step = EVENTS[event][0]
        reviewed = None
        if event in ("evidence_reviewed", "draft_reviewed"):
            refs = view["artifacts"][step]
            named = [ref for ref in refs if ref in text]
            if len(named) > 1 or (len(refs) > 1 and not named):
                raise ValueError("有多个可复核成果，请在反馈中指定一个 output_id。")
            current_basis = case_basis(case)
            for ref in named or refs:
                output = self.service.assets.get_output(case.case_id, ref)["output"]
                original = self.service.repository.get_case(case.case_id, output.case_version)
                if case_basis(original) == current_basis and (reviewed is None or output.case_version > reviewed.case_version):
                    reviewed = output
            if reviewed is None:
                raise ValueError("当前案件没有与现有案情相符的复核成果，请先生成或更新。")
            if step == "draft":
                artifacts = (reviewed.structured_data or {}).get("artifacts", {})
                if not artifacts or not all(self.service.assets.get_artifact(case.case_id, ref)["content"]
                                            for ref in artifacts.values()):
                    raise ValueError("起诉状缺少可读取的导出文件。")
        return view, reviewed

    def record(self, case, event, statement_id, actor_id, text, key):
        step, status, label = EVENTS[event]
        view, reviewed = self.validate_feedback(case, event, text)
        request = self._request(case, key + ":plan")
        plan = self.service.get_route_workflow({**request, "case_snapshot": case})
        if not plan.output_refs:
            raise ValueError(plan.summary)
        quote = text[:4000]
        note = (text if text.startswith(label + "：") else f"{label}：{text}")[:1750]
        if reviewed:
            note += f"（对应成果 {reviewed.output_id}）"
            if step == "draft" and reviewed.missing_fields:
                status = "in_progress"
            if step == "evidence" and (not reviewed.structured_data.get("entries") or
                                       reviewed.structured_data.get("supplementary_checklist")):
                status = "in_progress"
        changes = [dict(step_id=step, status=status, note=note, event=event)]
        if event.startswith("correct_"):
            changes.append(dict(step_id="submit", status="blocked", note=note, event=event))
        result = self.service.record_workflow_progress({
            **self._request(case, key + ":progress"), "expected_progress_version": view["progress_version"],
            "plan_output_id": plan.output_refs[0], "message_id": statement_id, "actor_id": actor_id,
            "quote": quote, "changes": changes,
        })
        if result.status == "error":
            raise ValueError(result.summary)
