"""Step-by-step guidance for the three resolution routes.

Step order, titles, materials and completion conditions come from routes.TEMPLATES.
This module only adds what the Agent needs to work on a step (the deliverable and
the tools that usually produce it) and reads/records user-confirmed progress
through the existing workflow progress service. A step is completed only by a
saved user statement; generated material never completes a step on its own.
"""
from __future__ import annotations

from .routes import TEMPLATES

ROUTES = tuple(TEMPLATES)

# step_id -> (deliverable the Agent should produce or obtain, tools that usually help)
STEP_WORK = {
    "negotiation": {
        "prepare": ("在回答中列出本案诉求、金额明细及依据，标出待核实项",
                    ["update_case", "check_evidence", "calculate_costs", "search_statutes"]),
        "communicate": ("给出可直接使用的协商提纲或沟通话术，并请用户反馈对方的回复",
                        ["calculate_costs", "search_statutes"]),
        "perform": ("核对协议要点和履行情况，列出未解决事项", ["check_evidence"]),
    },
    "mediation": {
        "channel": ("说明可用的调解渠道、申请条件和需要用户核实的事项", ["search_statutes"]),
        "participate": ("整理调解用的案情摘要、诉求清单和材料清单，并给出可谈的方案",
                        ["check_evidence", "calculate_costs", "parse_documents"]),
        "result": ("核对调解结果、协议效力和履行情况，说明后续可用程序", ["search_statutes"]),
    },
    "litigation": {
        "jurisdiction": ("核对原告、被告、诉求和管辖法院，缺什么问什么", ["update_case", "search_statutes"]),
        "evidence": ("读取材料并生成证据目录，列出补证清单", ["parse_documents", "check_evidence", "build_evidence_directory"]),
        "draft": ("生成起诉状草稿并说明待补字段", ["find_form_template", "draft_document", "verify_output"]),
        "submit": ("说明提交渠道和材料清单，等待用户报告实际提交或受理结果", ["search_statutes"]),
    },
}

DONE = ("completed", "not_applicable")


class RouteGuide:
    def __init__(self, service):
        self.service = service

    @staticmethod
    def _request(case, key):
        return dict(context=dict(case_id=case.case_id, request_id=key, deadline_at=None, experiment_run_id=None),
                    case_version=case.case_version, route=case.selected_route)

    def view(self, case, inventory=None):
        """Progress of the case's selected route, or None before a route is selected."""
        route = case.selected_route
        if route not in TEMPLATES:
            return None
        progress = self.service.workflow.latest_progress(case.case_id, route)
        outputs = self._step_outputs(case, inventory)
        steps = []
        for step_id, title, _, _, materials, condition, _ in TEMPLATES[route]:
            record = progress.records.get(step_id)
            deliverable, tools = STEP_WORK[route][step_id]
            steps.append(dict(step_id=step_id, title=title, status=record.status if record else "pending",
                              materials=materials, completion_condition=condition, deliverable=deliverable,
                              tools=tools, note=record.note if record else None,
                              output_refs=outputs.get(step_id, [])))
        current = next((s for s in steps if s["status"] not in DONE), None)
        return dict(route=route, current_step=current["step_id"] if current else None,
                    steps=steps, progress_version=progress.revision)

    def _step_outputs(self, case, inventory):
        if case.selected_route != "litigation":
            return {}
        inventory = inventory or self.service.assets.list_case_inventory(case.case_id)
        found = {"evidence": [], "draft": []}
        for output in sorted(inventory["outputs"], key=lambda o: (o.case_version, o.output_version), reverse=True):
            if output.output_type == "evidence_directory":
                found["evidence"].append(output.output_id)
            elif output.output_type == "document" and (output.template_id or "").startswith("cn_complaint_"):
                found["draft"].append(output.output_id)
        return found

    def complete_step(self, case, step_id, *, message_id, actor_id, quote, key, status="completed"):
        """Record a user-confirmed step result; the statement must already be saved."""
        view = self.view(case)
        if view is None:
            raise ValueError("请先选择处理路线")
        step = next((s for s in view["steps"] if s["step_id"] == step_id), None)
        if step is None:
            raise ValueError("该步骤不属于当前路线")
        plan = self.service.get_route_workflow({**self._request(case, key + ":plan"), "case_snapshot": case})
        if not plan.output_refs:
            raise ValueError(plan.summary)
        note = f"{step['title']}：{quote}"[:1900]
        result = self.service.record_workflow_progress({
            **self._request(case, key + ":progress"), "expected_progress_version": view["progress_version"],
            "plan_output_id": plan.output_refs[0], "message_id": message_id, "actor_id": actor_id,
            "quote": quote[:4000], "changes": [dict(step_id=step_id, status=status, note=note)],
        })
        if result.status == "error":
            raise ValueError(result.summary)
        return self.view(self.service.repository.get_case(case.case_id))
