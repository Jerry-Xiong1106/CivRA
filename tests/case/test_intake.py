"""Intake summaries and confirmation preparation preserve factual provenance."""

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.case import CaseService, SQLiteCaseRepository
from backend.case.contracts import CaseRequest, InvocationContext
from backend.case.intake import CaseIntakeService, FactReviewAction, FactReviewRequest
from backend.case.models import CaseSnapshot, Confirmation, Evidence, Fact, MaterialRef, UserStatement


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = SQLiteCaseRepository(Path(self.tmp.name) / "case.sqlite3")
        self.at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.reply_at = self.at + timedelta(days=1)
        self.message = UserStatement(message_id="reply", actor_id="user", text="合成测试中的明确结构化核对操作。", created_at=self.reply_at)
        self.ref = MaterialRef(file_id="file", file_version="1", page=1, quote="事故日期2026-05-13")
        self.case = self.repo.create_case(CaseSnapshot(case_id="case", case_version=1, synthetic=True,
            dispute_type="traffic_accident", jurisdiction=None, stage="consultation", selected_route=None,
            user_goals=["整理案情"], statements=[UserStatement(message_id="initial", actor_id="user",
                text="姓名甲，事故日期2026-05-12，保险暂不清楚。", created_at=self.at), self.message],
            evidence=[Evidence(evidence_id="e", file_id="file", file_version="1", file_name="合成材料.txt",
                media_type="text/plain", document_type="accident_record", content_sha256="a"*64,
                material_refs=[self.ref], status="available")],
            facts=[
                self.fact("name", "claimant_name", "text", "甲"),
                self.fact("date_a", "accident_date", "date", "2026-05-12"),
                Fact(fact_id="date_b", field="accident_date", value_type="date", value="2026-05-13",
                     provenance="document_record", confirmation_status="unconfirmed", evidence_ids=["e"], material_refs=[self.ref]),
                self.fact("insurance", "insurance", "text", None),
                self.fact("amount", "repair_amount", "money", {"amount":"3000.00", "currency":"CNY"}),
                self.fact("flag", "has_payment", "boolean", False),
            ], created_at=self.at, updated_at=self.reply_at))
        self.intake = CaseIntakeService(self.repo)

    def fact(self, key, field, kind, value):
        return Fact(fact_id=key, field=field, value_type=kind, value=value, provenance="user_statement",
                    confirmation_status="unconfirmed", statement_refs=["initial"])

    def context(self, request_id="request", case_id="case", deadline=None):
        return InvocationContext(request_id=request_id, case_id=case_id, experiment_run_id="synthetic-test", deadline_at=deadline)

    def summary(self, version=1):
        return self.intake.summarize(CaseRequest(context=self.context(), case_version=version))

    def review(self, actions, **changes):
        payload={"context":self.context(), "case_version":1, "message_id":"reply", "reviewer_id":"user", "actions":actions}
        payload.update(changes)
        return self.intake.prepare_review(payload)

    def save(self, prepared):
        self.assertEqual(prepared.status, "ok", prepared.error)
        result=CaseService(self.repo).update_case(prepared.data.update_request)
        self.assertEqual(result.status, "ok", result.error)
        return result

    def test_summary_keeps_candidates_sources_and_unconfirmed_states(self):
        before=self.repo.get_case("case").model_dump_json()
        result=self.summary()
        self.assertEqual(result.status,"insufficient")
        facts={item.fact.fact_id:item for item in result.data.facts}
        self.assertTrue(facts["date_a"].has_conflict)
        self.assertTrue(facts["date_b"].has_conflict)
        self.assertEqual(facts["date_b"].fact.material_refs[0],self.ref)
        self.assertEqual(facts["name"].group,"parties")
        self.assertEqual(facts["amount"].group,"amounts")
        self.assertEqual(facts["amount"].display_value,"3000.00 CNY")
        self.assertEqual(facts["insurance"].state,"unknown")
        self.assertEqual(facts["flag"].display_value,"否")
        self.assertIn("存在冲突",result.data.text)
        self.assertEqual(before,self.repo.get_case("case").model_dump_json())

    def test_summary_does_not_accept_injected_snapshot(self):
        payload=CaseRequest(context=self.context(),case_version=1).model_dump(mode="json")
        payload["case_snapshot"]=self.case.model_dump(mode="json")
        self.assertEqual(self.intake.summarize(payload).error.code,"invalid_request")

    def test_read_boundaries(self):
        requests=[(CaseRequest(context=self.context(),case_version=2),"version_conflict"),
            (CaseRequest(context=self.context(case_id="absent"),case_version=1),"not_found"),
            (CaseRequest(context=self.context(deadline=self.at),case_version=1),"timeout")]
        for request,code in requests:
            with self.subTest(code=code):
                self.assertEqual(self.intake.summarize(request).error.code,code)

    def test_empty_intake_retains_statement_without_inventing_facts(self):
        payload=self.case.model_dump(mode="json")
        payload.update(case_id="empty",facts=[],evidence=[],dispute_type=None)
        self.repo.create_case(CaseSnapshot.model_validate(payload))
        result=self.intake.summarize(CaseRequest(context=self.context(case_id="empty"),case_version=1))
        self.assertEqual(result.data.facts,[])
        self.assertEqual(len(result.data.statements),2)
        self.assertEqual({p.field for p in result.data.pending_items},{"case.dispute_type","case.facts"})

    def test_confirm_is_prepared_without_writing_then_uses_existing_update(self):
        prepared=self.review([{"fact_id":"name","action":"confirm"}])
        self.assertEqual(self.repo.get_case("case").case_version,1)
        saved=self.save(prepared)
        name=next(f for f in saved.data.case_snapshot.facts if f.fact_id=="name")
        self.assertEqual(name.confirmation.message_id,"reply")
        self.assertEqual(name.confirmation_status,"confirmed")
        self.assertEqual(self.repo.get_case("case",1).facts[0].confirmation_status,"unconfirmed")

    def test_confirm_one_candidate_does_not_resolve_another(self):
        self.save(self.review([{"fact_id":"date_a","action":"confirm"}]))
        result=self.summary(2)
        self.assertTrue(any(item.reason=="conflict" for item in result.data.pending_items))
        self.assertIsNone(next(f for f in self.repo.get_case("case").facts if f.fact_id=="date_b").superseded_by)

    def test_explicit_resolution_keeps_original_record(self):
        self.save(self.review([{"fact_id":"date_a","action":"confirm","supersede_fact_ids":["date_b"]}]))
        result=self.summary(2)
        old=next(f for f in result.data.facts if f.fact.fact_id=="date_b")
        self.assertEqual(old.state,"superseded")
        self.assertEqual(old.fact.value,"2026-05-13")
        self.assertEqual(old.fact.material_refs,[self.ref])
        self.assertFalse(any(item.reason=="conflict" for item in result.data.pending_items))

    def test_correction_adds_user_fact_without_rewriting_document(self):
        prepared=self.review([{"fact_id":"date_b","action":"correct","new_fact_id":"date_new",
            "replacement_value":"2026-05-14","supersede_fact_ids":["date_a"]}])
        saved=self.save(prepared).data.case_snapshot
        new=next(f for f in saved.facts if f.fact_id=="date_new")
        self.assertEqual(new.statement_refs,["reply"])
        self.assertEqual(new.provenance,"user_statement")
        self.assertEqual(new.material_refs,[])
        self.assertEqual(next(f for f in saved.facts if f.fact_id=="date_b").value,"2026-05-13")

    def test_unknown_and_invalid_correction_rejected(self):
        actions=[{"fact_id":"insurance","action":"confirm"},
            {"fact_id":"name","action":"correct","new_fact_id":"new","replacement_value":"暂不清楚"},
            {"fact_id":"date_a","action":"correct","new_fact_id":"new","replacement_value":"2026-02-30"},
            {"fact_id":"amount","action":"correct","new_fact_id":"new","replacement_value":"3000"}]
        for action in actions:
            with self.subTest(action=action):
                self.assertEqual(self.review([action]).error.code,"invalid_request")
        self.assertEqual(self.repo.get_case("case").case_version,1)

    def test_wrong_identity_and_unknown_message_rejected(self):
        for overrides in ({"reviewer_id":"someone_else"},{"message_id":"absent"}):
            with self.subTest(overrides=overrides):
                self.assertEqual(self.review([{"fact_id":"name","action":"confirm"}],**overrides).error.code,"invalid_request")

    def test_defer_never_confirms_or_changes_case(self):
        result=self.review([{"fact_id":"name","action":"defer"}])
        self.assertIsNone(result.data.update_request)
        self.assertEqual(result.data.deferred_fact_ids,["name"])
        self.assertEqual(self.repo.get_case("case").case_version,1)

    def test_dispute_preserves_value_and_history(self):
        self.save(self.review([{"fact_id":"name","action":"confirm"}]))
        prepared=self.review([{"fact_id":"name","action":"dispute"}],case_version=2,context=self.context("dispute"))
        result=self.save(prepared)
        fact=next(f for f in result.data.case_snapshot.facts if f.fact_id=="name")
        self.assertEqual((fact.value,fact.confirmation_status,fact.confirmation),("甲","disputed",None))
        self.assertIsNotNone(self.repo.get_case("case",2).facts[0].confirmation)

    def test_invalid_batch_is_atomic_and_overlap_is_rejected(self):
        batches=[
            [{"fact_id":"name","action":"confirm"},{"fact_id":"missing","action":"confirm"}],
            [{"fact_id":"date_a","action":"confirm","supersede_fact_ids":["date_b"]},{"fact_id":"date_b","action":"defer"}],
            [{"fact_id":"date_a","action":"confirm","supersede_fact_ids":["name"]}],
            [{"fact_id":"name","action":"correct","new_fact_id":"date_a","replacement_value":"乙"}],
            [{"fact_id":"date_a","action":"confirm"},{"fact_id":"date_b","action":"confirm"}],
        ]
        for actions in batches:
            with self.subTest(actions=actions):
                result=self.review(actions)
                self.assertEqual(result.status,"error")
                self.assertIsNone(result.data)
                self.assertEqual(self.repo.get_case("case").case_version,1)

    def test_prepared_update_retry_does_not_increment_version_twice(self):
        prepared=self.review([{"fact_id":"name","action":"confirm"}])
        self.save(prepared)
        self.save(prepared)
        self.assertEqual(self.repo.get_case("case").case_version,2)

    def test_excluded_source_remains_visible_but_cannot_be_confirmed(self):
        payload=self.case.model_dump(mode="json")
        payload["case_id"]="excluded"
        payload["evidence"][0].update(status="excluded",exclusion=Confirmation(message_id="reply",confirmed_by="user",confirmed_at=self.reply_at).model_dump(mode="json"))
        self.repo.create_case(CaseSnapshot.model_validate(payload))
        context=self.context(case_id="excluded")
        result=self.intake.summarize(CaseRequest(context=context,case_version=1))
        self.assertEqual(next(f for f in result.data.facts if f.fact.fact_id=="date_b").state,"excluded_source")
        self.assertEqual(self.review([{"fact_id":"date_b","action":"confirm"}],context=context).error.code,"invalid_request")

    def test_fact_review_contract_rejects_unrelated_actions(self):
        for action in ({"fact_id":"name","action":"defer","supersede_fact_ids":["date_a"]},
                       {"fact_id":"name","action":"confirm","replacement_value":"乙"}):
            with self.subTest(action=action):
                self.assertEqual(self.review([action]).status,"error")

    def test_summary_result_is_detached_from_storage(self):
        result=self.summary()
        result.data.statements[0].text="changed in caller"
        result.data.facts[0].fact.value="changed in caller"
        self.assertNotEqual(self.repo.get_case("case").statements[0].text,"changed in caller")

    def test_review_checks_version_and_deadline(self):
        result=self.review([{"fact_id":"name","action":"confirm"}],case_version=2)
        self.assertEqual(result.error.code,"version_conflict")
        result=self.review([{"fact_id":"name","action":"confirm"}],context=self.context(deadline=self.at))
        self.assertEqual(result.error.code,"timeout")


if __name__ == "__main__":
    unittest.main()
