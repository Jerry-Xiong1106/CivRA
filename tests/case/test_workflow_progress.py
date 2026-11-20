"""Persistent task lifecycle, provenance, stale basis and compatibility checks."""

from backend.case.intake.updates import set_case_stage, record_user_statement
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backend.case import CaseService, SQLiteCaseRepository
from scripts.case.fixtures import load_development_case
from backend.case.planning.contracts import RouteWorkflowRequest
from backend.case.models import CaseSnapshot
from backend.case.planning.routes import get_route_workflow
from backend.case.planning.workflow_models import WORKFLOW_VERSION


class WorkflowProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.repo = SQLiteCaseRepository(Path(self.temp.name) / "cases.db")
        self.case = self.repo.create_case(load_development_case())
        set_case_stage(self.repo, self.case.case_id, "litigation")
        self.service = CaseService(self.repo)
        self.count = 0

    def context(self, request_id=None):
        self.count += 1
        return dict(request_id=request_id or f"workflow-{self.count}", case_id=self.case.case_id,
                    experiment_run_id=None, deadline_at=None)

    def plan_request(self, **changes):
        snapshot = self.repo.get_case(self.case.case_id)
        return dict(context=self.context(), case_version=snapshot.case_version, case_snapshot=snapshot,
                    route="litigation", **changes)

    def plan(self, **changes):
        result = self.service.get_route_workflow(self.plan_request(**changes))
        self.assertEqual(result.status, "insufficient", result.error)
        return result

    def reply(self):
        snapshot = self.repo.get_case(self.case.case_id)
        self.message_id = f"reply-{self.count}"
        self.quote = "我确认已核对管辖，材料准备仍有问题；后续以我的明确反馈更新。"
        result = record_user_statement(self.repo, case_id=snapshot.case_id, expected_case_version=snapshot.case_version,
            request_id="save-" + self.message_id, message_id=self.message_id, actor_id="synthetic_user", text=self.quote,
            created_at=max(datetime.now(timezone.utc), snapshot.updated_at))
        self.assertEqual(result.status, "ok", result.error)

    def progress_request(self, plan, status="completed", **changes):
        snapshot = self.repo.get_case(self.case.case_id)
        request = dict(context=self.context(), case_version=snapshot.case_version, route="litigation",
            expected_progress_version=plan.data.progress_version, plan_output_id=plan.output_refs[0],
            message_id=self.message_id, actor_id="synthetic_user", quote=self.quote,
            changes=[dict(step_id="jurisdiction", status=status, note="用户明确反馈本项状态", evidence_ids=[])])
        request.update(changes)
        return request

    def replace(self, mutate):
        snapshot = self.repo.get_case(self.case.case_id)
        raw = snapshot.model_dump(mode="json"); mutate(raw)
        raw["case_version"] += 1
        raw["updated_at"] = max(datetime.now(timezone.utc), snapshot.updated_at).isoformat()
        return self.repo.replace_case(CaseSnapshot.model_validate(raw), expected_case_version=snapshot.case_version)

    def test_core_steps_remain_compatible_and_optional_nodes_are_conditional(self):
        result = self.plan()
        self.assertEqual(result.data.template_version, WORKFLOW_VERSION)
        self.assertEqual(len(result.data.steps), 4)
        self.assertEqual({item.step_id for item in result.data.conditional_steps},
                         {"insurance_review", "performance_review", "enforcement_review"})
        self.assertTrue(all(item.conditional and not item.should_skip for item in result.data.conditional_steps))
        self.assertTrue(all(item.deadline is None for item in result.data.steps))
        self.assertTrue(result.data.findings)
        ids = {item.step_id for item in result.data.steps + result.data.conditional_steps}
        self.assertTrue(all(edge.from_step in ids and edge.to_step in ids for edge in result.data.edges))

    def test_progress_survives_service_restart_and_completion_does_not_close_case(self):
        self.reply(); plan = self.plan()
        before = self.repo.get_case(self.case.case_id)
        result = self.service.record_workflow_progress(self.progress_request(plan))
        self.assertEqual(result.status, "ok", result.error)
        self.assertEqual(result.data.revision, 1)
        self.service = CaseService(SQLiteCaseRepository(self.repo.path))
        updated = self.plan()
        self.assertTrue(updated.data.steps[0].should_skip)
        self.assertNotIn("jurisdiction", updated.data.pending_step_ids)
        self.assertEqual(self.repo.get_case(self.case.case_id), before)
        read = self.service.get_workflow_progress(dict(context=self.context(), case_version=before.case_version, route="litigation"))
        self.assertEqual(read.data.records["jurisdiction"].message_id, self.message_id)

    def test_message_only_changes_preserve_progress_basis(self):
        plan = self.plan(); self.reply()
        result = self.service.record_workflow_progress(self.progress_request(plan))
        self.assertEqual(result.status, "ok", result.error)
        self.assertTrue(self.plan().data.steps[0].should_skip)

    def test_substantive_change_keeps_history_and_requires_review(self):
        self.reply(); plan = self.plan()
        self.assertEqual(self.service.record_workflow_progress(self.progress_request(plan)).status, "ok")
        self.replace(lambda raw: raw.update(user_goals=["changed goal"]))
        step = self.plan().data.steps[0]
        self.assertEqual(step.status, "needs_review")
        self.assertEqual(step.progress.status, "completed")
        self.assertFalse(step.should_skip)

    def test_reopen_and_blocked_status_append_history(self):
        self.reply(); first = self.plan()
        saved = self.service.record_workflow_progress(self.progress_request(first))
        second = self.plan()
        blocked = self.service.record_workflow_progress(self.progress_request(second, "blocked"))
        self.assertEqual(blocked.data.revision, 2)
        current = self.plan()
        self.assertEqual(current.data.steps[0].status, "blocked")
        self.assertFalse(current.data.steps[0].should_skip)
        history = self.service.assets.get_output(self.case.case_id, saved.output_refs[0], 1)["output"]
        self.assertEqual(history.structured_data["_business_result"]["data"]["records"]["jurisdiction"]["status"], "completed")
        reopened = self.service.record_workflow_progress(self.progress_request(current, "pending"))
        self.assertEqual(reopened.data.revision, 3)

    def test_not_applicable_is_explicit_user_status_not_legal_decision(self):
        self.reply(); plan = self.plan()
        request = self.progress_request(plan, changes=[dict(step_id="insurance_review", status="not_applicable", note="用户标记暂不处理保险事项")])
        self.assertEqual(self.service.record_workflow_progress(request).status, "ok")
        insurance = self.plan().data.conditional_steps[0]
        self.assertTrue(insurance.should_skip)
        self.assertIn("未认定", insurance.applicability)

    def test_unknown_message_actor_or_nonliteral_quote_rejected(self):
        self.reply(); plan = self.plan()
        for field, value in (("message_id", "unknown"), ("actor_id", "other"), ("quote", "虚构回复")):
            with self.subTest(field=field):
                result = self.service.record_workflow_progress(self.progress_request(plan, **{field: value}))
                self.assertEqual(result.error.code, "invalid_request")
        self.assertEqual(self.service.workflow._read(self.case.case_id, "litigation")[0].revision, 0)

    def test_unknown_steps_or_evidence_rejected_atomically(self):
        self.reply(); plan = self.plan()
        for change in (dict(step_id="bogus", status="completed", note="test"),
                       dict(step_id="submit", status="completed", note="test", evidence_ids=["bogus"])):
            request = self.progress_request(plan)
            request["changes"].append(change)
            self.assertEqual(self.service.record_workflow_progress(request).error.code, "invalid_request")
        self.assertEqual(self.service.workflow._read(self.case.case_id, "litigation")[0].revision, 0)

    def test_stale_plan_after_case_change_cannot_record_completion(self):
        self.reply(); plan = self.plan()
        self.replace(lambda raw: raw.update(user_goals=["changed"]))
        result = self.service.record_workflow_progress(self.progress_request(plan))
        self.assertEqual(result.error.code, "invalid_request")

    def test_old_progress_version_rejected_and_request_ids_are_not_reused(self):
        self.reply(); request = self.plan_request(); plan = self.service.get_route_workflow(request)
        self.assertEqual(self.service.record_workflow_progress(self.progress_request(plan)).status, "ok")
        self.assertEqual(self.service.get_route_workflow({**self.plan_request(), "progress_version": 0}).error.code, "invalid_request")
        self.assertEqual(self.service.get_route_workflow(request).error.code, "invalid_request")

    def test_record_retry_returns_original_response_and_reuse_is_rejected(self):
        self.reply(); request = self.progress_request(self.plan())
        saved = self.service.record_workflow_progress(request)
        self.assertEqual(saved, self.service.record_workflow_progress(request))
        self.service.record_workflow_progress(self.progress_request(self.plan(), "pending"))
        self.assertEqual(saved, self.service.record_workflow_progress(request))
        changed = deepcopy(request); changed["changes"][0]["status"] = "blocked"
        self.assertEqual(self.service.record_workflow_progress(changed).error.code, "invalid_request")

    def test_concurrent_writes_allow_one_revision_only(self):
        self.reply(); plan = self.plan()
        requests = [self.progress_request(plan, "completed"), self.progress_request(plan, "blocked")]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.service.record_workflow_progress, requests))
        self.assertEqual(sum(result.status == "ok" for result in results), 1)
        loser = next(result for result in results if result.status == "error")
        self.assertEqual(loser.error.code, "version_conflict")
        self.assertEqual(self.service.workflow._read(self.case.case_id, "litigation")[0].revision, 1)

    def test_concurrent_identical_retry_is_idempotent(self):
        self.reply(); request = self.progress_request(self.plan())
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.service.record_workflow_progress, [request, request]))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0].status, "ok")

    def test_route_mismatch_does_not_save_progress(self):
        self.reply(); request = self.progress_request(self.plan(), route="mediation")
        self.assertEqual(self.service.record_workflow_progress(request).error.code, "invalid_request")
        self.assertEqual(self.service.get_route_workflow({**self.plan_request(), "route": "mediation"}).error.code, "invalid_request")

    def test_expired_record_and_plan_save_nothing(self):
        self.reply(); request = self.progress_request(self.plan())
        request["context"]["deadline_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.assertEqual(self.service.record_workflow_progress(request).error.code, "timeout")
        request = self.plan_request(); request["context"]["deadline_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.assertEqual(self.service.get_route_workflow(request).error.code, "timeout")
        self.assertEqual(self.service.workflow._read(self.case.case_id, "litigation")[0].revision, 0)

    def test_source_note_is_located_but_not_copied_into_contact(self):
        pointer = self.service.calculator.fee_rule.pointer
        quote = self.service.get_source(pointer)["text"]
        result = self.plan(available_authorities=[pointer], source_notes=[dict(note_id="contact-note", step_id="jurisdiction",
            aspect="contact", note="调用方候选地址，不代表正确", source_ref=pointer, quote=quote)])
        self.assertEqual(result.data.source_checks[0].status, "original_text_checked")
        self.assertFalse(result.data.source_checks[0].semantic_support_checked)
        self.assertIsNone(result.data.steps[0].contact)
        self.assertEqual(result.data.steps[0].source_refs, [pointer])

    def test_source_failure_preserves_workflow_and_timeout_does_not_save(self):
        pointer = self.service.calculator.fee_rule.pointer
        result = self.plan(available_authorities=[pointer], source_notes=[dict(note_id="bad", step_id="submit",
            aspect="conditions", note="测试", source_ref=pointer, quote="not found")])
        self.assertEqual(result.data.source_checks[0].status, "rejected")
        self.assertFalse(result.source_refs)

    def test_explicit_dates_are_not_computed_legal_deadlines(self):
        def add_date(raw):
            fact = deepcopy(raw["facts"][2]); fact.update(fact_id="target-date", field="user_target_date", value_type="date", value="2026-11-30")
            raw["facts"].append(fact)
        self.replace(add_date)
        step = self.plan(dates=[dict(step_id="submit", fact_id="target-date", kind="user_target")]).data.steps[-1]
        self.assertEqual(step.deadline, "2026-11-30")
        self.assertEqual(step.deadline_kind, "user_target")
        self.assertFalse(step.deadline_verified)
        wrong = self.plan(dates=[dict(step_id="submit", fact_id="target-date", kind="material_record")])
        self.assertIsNone(wrong.data.steps[-1].deadline)

    def test_unconfirmed_and_conflicting_date_remain_unknown(self):
        result = self.plan(dates=[dict(step_id="submit", fact_id="f_date_user", kind="user_target")])
        self.assertIsNone(result.data.steps[-1].deadline)

    def test_annotation_unknown_steps_and_duplicates_rejected(self):
        result = self.service.get_route_workflow(self.plan_request(dates=[dict(step_id="bad", fact_id="f_date_user", kind="user_target")]))
        self.assertEqual(result.error.code, "invalid_request")
        date = dict(step_id="submit", fact_id="f_date_user", kind="user_target")
        self.assertEqual(self.service.get_route_workflow(self.plan_request(dates=[date, date])).error.code, "invalid_request")

    def test_cross_case_plan_is_not_readable(self):
        self.reply()
        request = self.progress_request(self.plan(), plan_output_id="get_route_workflow_other_case")
        self.assertEqual(self.service.record_workflow_progress(request).error.code, "not_found")

    def test_legacy_digest_and_standalone_default(self):
        request = RouteWorkflowRequest.model_validate(self.plan_request())
        raw = request.model_dump(mode="json")
        for field in ("progress_version", "source_notes", "dates"): raw.pop(field)
        expected = hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(self.service.outputs.request_digest(request), expected)
        self.assertEqual(get_route_workflow(request).data.progress_version, 0)
        self.assertEqual(get_route_workflow(request.model_copy(update={"progress_version": 1})).status, "error")


if __name__ == "__main__":
    unittest.main()
