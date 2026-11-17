"""Business boundary checks: independent cases, conflicts, source fidelity and atomic writes."""

import json
import sqlite3
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.case import CaseService, SQLiteCaseRepository, load_development_case
from backend.case.materials.contracts import BuildEvidenceDirectoryRequest, CheckEvidenceRequest
from backend.case.intake.contracts import CaseChanges, UpdateCaseRequest
from backend.case.contracts import InvocationContext
from backend.case.models import CaseSnapshot, Fact, UserStatement
from backend.case.errors import CaseNotFound, VersionConflict
from backend.agent.case_adapter import RepositoryCasePort

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/case"))
from demo_support import DEMO_REPLY, confirmed_demo_changes


class BusinessTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Path(self.directory.name) / "cases.sqlite3"
        self.repo = SQLiteCaseRepository(self.db)
        self.case = self.repo.create_case(load_development_case())
        self.service = CaseService(self.repo)

    def context(self, request_id="test", case_id=None, deadline=None):
        return InvocationContext(request_id=request_id, case_id=case_id or self.case.case_id,
                                 experiment_run_id="test-only", deadline_at=deadline)

    def check(self, snapshot=None):
        snapshot = snapshot or self.repo.get_case(self.case.case_id)
        return self.service.check_evidence(CheckEvidenceRequest(context=self.context(case_id=snapshot.case_id),
            case_version=snapshot.case_version, case_snapshot=snapshot))

    def new_fact_request(self, request_id="update-1", expected=1, value="unknown insurance details"):
        return UpdateCaseRequest(context=self.context(request_id), expected_case_version=expected,
            changes=CaseChanges(upsert_facts=[Fact(fact_id="f_insurance_new", field="insurance",
                value_type="text", value=value, provenance="user_statement", confirmation_status="unconfirmed",
                statement_refs=["msg_001"])]), change_reason="new user-provided fact")

    def resolve(self):
        port = RepositoryCasePort(self.repo)
        recorded = port.record_user_reply(case_id=self.case.case_id, expected_case_version=1,
            request_id="record", message_id="msg_demo_reply", actor_id="synthetic_user", text=DEMO_REPLY,
            created_at=datetime.now(timezone.utc))
        self.assertEqual(recorded.status, "ok")
        snapshot = self.repo.get_case(self.case.case_id)
        result = self.service.update_case(UpdateCaseRequest(context=self.context("confirm"),
            expected_case_version=snapshot.case_version, changes=confirmed_demo_changes(snapshot), change_reason="explicit user reply"))
        self.assertEqual(result.status, "ok", result.error)
        return result.data.case_snapshot

    def test_actual_fields_conflict_duplicates_and_locations(self):
        result = self.check()
        self.assertEqual(result.status, "insufficient")
        report = result.data
        self.assertEqual({field for item in report.missing_facts for field in item.affected_fields}, {"insurance", "liability_finding"})
        self.assertEqual(report.conflicts[0].affected_fields, ["accident_date"])
        self.assertEqual(set(report.conflicts[0].fact_ids), {"f_date_user", "f_date_document"})
        self.assertTrue(report.conflicts[0].material_refs)
        self.assertEqual(set(report.duplicates[0].evidence_ids), {"e_invoice", "e_invoice_copy"})
        self.assertEqual(len(report.suggested_questions), 4)

    def test_rule_engine_does_not_read_expected_review(self):
        import inspect
        import backend.case.materials.evidence as module
        self.assertNotIn("expected_review", inspect.getsource(module))
        payload = self.case.model_dump(mode="json")
        payload["facts"][0]["value"] = "2026-05-13"
        payload["evidence"][2]["content_sha256"] = "f" * 64
        report = self.check(CaseSnapshot.model_validate(payload)).data
        self.assertEqual(report.conflicts, [])
        self.assertEqual(report.duplicates, [])

    def test_amount_representation_is_not_a_conflict(self):
        payload = self.case.model_dump(mode="json")
        payload["facts"][4]["value"]["amount"] = "3000.0"
        self.assertFalse(any(item.affected_fields == ["repair_amount"] for item in self.check(CaseSnapshot.model_validate(payload)).data.conflicts))
        payload["facts"][4]["value"]["amount"] = "4000.00"
        self.assertTrue(any(item.affected_fields == ["repair_amount"] for item in self.check(CaseSnapshot.model_validate(payload)).data.conflicts))

    def test_missing_claim_material_is_reported(self):
        payload = self.case.model_dump(mode="json")
        payload["claims"][0]["fact_ids"] = []
        payload["claims"][0]["evidence_ids"] = []
        report = self.check(CaseSnapshot.model_validate(payload)).data
        self.assertEqual(len(report.material_gaps), 3)
        self.assertTrue(all(item.category == "missing_evidence" for item in report.material_gaps))

    def test_duplicate_detection_does_not_exclude_or_change_amount(self):
        before = self.case.model_dump(mode="json")
        self.check(self.case)
        self.assertEqual(self.case.model_dump(mode="json"), before)
        self.assertEqual(self.repo.get_case(self.case.case_id).case_version, 1)
        self.assertEqual(self.case.claims[0].amount.amount, "3000.00")

    def test_directory_requires_user_litigation_selection(self):
        result = self.service.build_evidence_directory(BuildEvidenceDirectoryRequest(context=self.context(),
            case_version=1, case_snapshot=self.case))
        self.assertEqual(result.status, "error")

    def test_unresolved_litigation_directory_retains_conflicts_and_gaps(self):
        port = RepositoryCasePort(self.repo)
        port.set_stage(self.case.case_id, "litigation")
        case = self.repo.get_case(self.case.case_id)
        result = self.service.build_evidence_directory(BuildEvidenceDirectoryRequest(context=self.context(),
            case_version=case.case_version, case_snapshot=case))
        self.assertEqual(result.status, "insufficient")
        self.assertEqual(len(result.data.entries), 3)
        self.assertEqual(len(result.data.supplementary_checklist), 4)
        self.assertTrue(any(item.proof_purposes for item in result.data.entries))
        self.assertTrue(any(item.warnings for item in result.data.entries))

    def test_user_resolution_preserves_original_text_and_excluded_material(self):
        current = self.resolve()
        self.assertEqual(self.check(current).status, "ok")
        original = next(fact for fact in current.facts if fact.fact_id == "f_date_document")
        self.assertEqual(original.value, "2026-05-13")
        self.assertEqual(original.superseded_by, "f_date_user")
        excluded = next(item for item in current.evidence if item.evidence_id == "e_invoice_copy")
        self.assertEqual(excluded.status, "excluded")
        self.assertEqual(excluded.exclusion.message_id, "msg_demo_reply")
        self.assertIsNone(self.repo.get_case(self.case.case_id, 1).facts[1].superseded_by)

    def test_confirming_one_value_does_not_silently_remove_other_value(self):
        payload = self.case.model_dump(mode="json")
        payload["facts"][0]["confirmation_status"] = "confirmed"
        payload["facts"][0]["confirmation"] = payload["facts"][2]["confirmation"]
        self.assertEqual(len(self.check(CaseSnapshot.model_validate(payload)).data.conflicts), 1)

    def test_resolution_requires_valid_confirmed_replacement(self):
        payload = self.case.model_dump(mode="json")
        payload["facts"][1]["superseded_by"] = "f_date_user"
        with self.assertRaises(ValueError):
            CaseSnapshot.model_validate(payload)

    def test_exclusion_requires_explicit_confirmation(self):
        payload = self.case.model_dump(mode="json")
        payload["evidence"][2]["status"] = "excluded"
        with self.assertRaises(ValueError):
            CaseSnapshot.model_validate(payload)

    def test_persistence_history_and_reopen(self):
        result = self.service.update_case(self.new_fact_request())
        self.assertEqual(result.case_version, 2)
        reopened = SQLiteCaseRepository(self.db)
        self.assertEqual(reopened.get_case(self.case.case_id).facts[-1].field, "insurance")
        self.assertEqual(len(reopened.get_case(self.case.case_id, 1).facts), 5)
        history = reopened.get_history(self.case.case_id)
        self.assertEqual([item.case_version for item in history], [1, 2])
        self.assertEqual(history[-1].changed_fact_ids, ["f_insurance_new"])

    def test_stale_write_does_not_overwrite_current_state(self):
        self.service.update_case(self.new_fact_request())
        result = self.service.update_case(self.new_fact_request("another", value="different"))
        self.assertEqual(result.error.code, "version_conflict")
        self.assertEqual(result.error.details["actual_case_version"], 2)
        self.assertEqual(len(self.repo.get_history(self.case.case_id)), 2)

    def test_same_request_is_idempotent_after_reopen(self):
        request = self.new_fact_request()
        first = self.service.update_case(request)
        replay = CaseService(SQLiteCaseRepository(self.db)).update_case(request)
        self.assertEqual(first, replay)
        self.assertEqual(self.repo.get_case(self.case.case_id).case_version, 2)

    def test_user_reply_retry_reuses_original_receipt_timestamp(self):
        port = RepositoryCasePort(self.repo)
        arguments = dict(case_id=self.case.case_id, expected_case_version=1, request_id="reply-retry",
            message_id="msg_retry", actor_id="synthetic_user", text="固定用户回复", created_at=datetime.now(timezone.utc))
        first = port.record_user_reply(**arguments)
        replay = port.record_user_reply(**arguments)
        self.assertEqual(first.status, "ok")
        self.assertEqual(first, replay)
        self.assertEqual(self.repo.get_case(self.case.case_id).case_version, 2)

    def test_request_id_cannot_be_reused_with_different_payload(self):
        self.service.update_case(self.new_fact_request())
        result = self.service.update_case(self.new_fact_request(value="different"))
        self.assertEqual(result.error.code, "invalid_request")
        self.assertEqual(self.repo.get_case(self.case.case_id).case_version, 2)

    def test_two_concurrent_versions_allow_one_writer(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.service.update_case,
                [self.new_fact_request("left", value="left"), self.new_fact_request("right", value="right")]))
        self.assertEqual(sorted(result.status for result in results), ["error", "ok"])
        self.assertEqual(next(result for result in results if result.status == "error").error.code, "version_conflict")
        self.assertEqual(self.repo.get_case(self.case.case_id).case_version, 2)

    def test_concurrent_identical_retries_commit_once(self):
        request = self.new_fact_request()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.service.update_case, [request, request]))
        self.assertTrue(all(result.status == "ok" for result in results))
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.repo.get_case(self.case.case_id).case_version, 2)

    def test_invalid_graph_rolls_back_and_does_not_cache_failure(self):
        request = self.new_fact_request()
        bad = request.model_dump(mode="json")
        bad["changes"]["upsert_facts"][0]["statement_refs"] = ["missing-message"]
        result = self.service.update_case(bad)
        self.assertEqual(result.error.code, "invalid_request")
        self.assertEqual(self.repo.get_case(self.case.case_id).case_version, 1)
        self.assertEqual(self.service.update_case(request).status, "ok")

    def test_original_fact_and_message_cannot_be_rewritten(self):
        fact = self.case.facts[0].model_copy(update={"value": "2026-05-20"})
        request = UpdateCaseRequest(context=self.context("rewrite"), expected_case_version=1,
            changes=CaseChanges(upsert_facts=[fact]), change_reason="rewrite")
        self.assertEqual(self.service.update_case(request).error.code, "invalid_request")
        statement = self.case.statements[0].model_copy(update={"text": "modified original"})
        request = UpdateCaseRequest(context=self.context("rewrite-message"), expected_case_version=1,
            changes=CaseChanges(upsert_statements=[statement]), change_reason="rewrite")
        self.assertEqual(self.service.update_case(request).error.code, "invalid_request")

    def test_deadline_does_not_mutate(self):
        request = self.new_fact_request().model_dump(mode="json")
        request["context"]["deadline_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        self.assertEqual(self.service.update_case(request).error.code, "timeout")
        self.assertEqual(self.repo.get_case(self.case.case_id).case_version, 1)

    def test_deadline_is_rechecked_after_waiting_for_database_lock(self):
        lock = sqlite3.connect(self.db, isolation_level=None)
        self.addCleanup(lock.close)
        lock.execute("BEGIN IMMEDIATE")
        request = self.new_fact_request().model_dump(mode="json")
        request["context"]["deadline_at"] = (datetime.now(timezone.utc) + timedelta(seconds=0.15)).isoformat()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.service.update_case, request)
            time.sleep(0.25)
            lock.rollback()
            result = future.result(timeout=3)
        self.assertEqual(result.error.code, "timeout")
        self.assertEqual(self.repo.get_case(self.case.case_id).case_version, 1)

    def test_cases_are_isolated_even_when_fact_ids_and_request_ids_repeat(self):
        other = self.case.model_dump(mode="json")
        other["case_id"] = "synthetic_other_case"
        self.repo.create_case(CaseSnapshot.model_validate(other))
        self.service.update_case(self.new_fact_request())
        self.assertEqual(self.repo.get_case(other["case_id"]).case_version, 1)
        self.assertEqual(len(self.repo.get_case(other["case_id"]).facts), 5)

    def test_directory_after_resolution_keeps_only_active_evidence(self):
        current = self.resolve()
        RepositoryCasePort(self.repo).set_stage(current.case_id, "litigation")
        current = self.repo.get_case(current.case_id)
        result = self.service.build_evidence_directory(BuildEvidenceDirectoryRequest(context=self.context(),
            case_version=current.case_version, case_snapshot=current))
        self.assertEqual([item.evidence_id for item in result.data.entries], ["e_accident", "e_invoice"])
        self.assertEqual(result.data.excluded_evidence_ids, ["e_invoice_copy"])
        self.assertEqual(result.case_version, 4)
        self.assertEqual(self.repo.get_case(current.case_id, 1).case_version, 1)

    def test_generic_profile_does_not_invent_traffic_requirements(self):
        payload = self.case.model_dump(mode="json")
        payload["dispute_type"] = "leasing"
        report = self.check(CaseSnapshot.model_validate(payload)).data
        self.assertEqual(report.rule_profile, "generic_v1")
        self.assertEqual(report.missing_facts, [])
        self.assertTrue(report.limitations)

    def test_unknown_text_is_not_sufficient_information(self):
        payload = self.case.model_dump(mode="json")
        payload["facts"].append({"fact_id": "unknown_insurance", "field": "insurance", "value_type": "text",
            "value": "不知道", "provenance": "user_statement", "confirmation_status": "unconfirmed", "statement_refs": ["msg_001"]})
        report = self.check(CaseSnapshot.model_validate(payload)).data
        self.assertTrue(any(item.affected_fields == ["insurance"] for item in report.missing_facts))

    def test_invalid_request_and_unknown_case(self):
        self.assertEqual(self.service.check_evidence({}).error.code, "invalid_request")
        request = self.new_fact_request().model_dump(mode="json")
        request["context"]["case_id"] = "does-not-exist"
        self.assertEqual(self.service.update_case(request).error.code, "not_found")


if __name__ == "__main__":
    unittest.main()
