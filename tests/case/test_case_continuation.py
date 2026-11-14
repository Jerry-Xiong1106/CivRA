from datetime import datetime,timezone,timedelta
from pathlib import Path
import unittest
from backend.case import CaseService, SQLiteCaseRepository
from backend.case.models import OutputLink
from features10_13_support import FeatureFixture


class ContinuationTests(FeatureFixture,unittest.TestCase):
    def test_history_has_stable_upper_boundary_across_new_updates(self):
        self.advance();self.advance()
        first=self.service.get_case_history(self.request(snapshot=False,limit=1))
        self.assertEqual(first.data.through_version,3)
        self.assertEqual(first.data.next_after_version,1)
        self.advance()
        page=self.service.get_case_history(self.request(snapshot=False,after_version=1,through_version=3))
        self.assertEqual([i.case_version for i in page.data.items],[2,3])
        self.assertIsNone(page.data.next_after_version)

    def test_diff_preserves_before_after_and_old_snapshot(self):
        self.advance(lambda raw:raw["facts"][2].update(value="更新姓名"))
        result=self.service.compare_case_versions(self.request(snapshot=False,from_version=1,to_version=2))
        self.assertTrue(result.data.substantive_changed)
        change=result.data.changes["facts"]["modified"][0]
        self.assertEqual(change["before"]["value"],"示例甲")
        self.assertEqual(change["after"]["value"],"更新姓名")
        self.assertEqual(self.repo.get_case(self.case.case_id,1),self.case)

    def test_changed_fact_marks_old_dependent_output_for_review(self):
        output=self.output(links=[OutputLink(output_location="text",fact_ids=["f_claimant"])])
        self.advance(lambda raw:raw["facts"][2].update(value="新姓名"))
        result=self.service.prepare_case_continuation(self.request(snapshot=False,since_version=1))
        view=result.data.outputs[0]
        self.assertTrue(view["stale"])
        self.assertEqual(view["changed_dependencies"]["fact_ids"],["f_claimant"])
        self.assertTrue(view["needs_review"])
        self.assertEqual(self.service.assets.get_output(self.case.case_id,output.output_id)["output"],output)

    def test_each_output_is_compared_to_its_own_baseline(self):
        self.output("older",links=[OutputLink(output_location="text",fact_ids=["f_claimant"])])
        self.advance(lambda raw:raw["facts"][2].update(value="新姓名"))
        self.output("newer")
        result=self.service.prepare_case_continuation(self.request(snapshot=False,since_version=2))
        views={o["output_id"]:o for o in result.data.outputs}
        self.assertTrue(views["older"]["needs_review"])
        self.assertFalse(views["newer"]["needs_review"])

    def test_new_uploaded_file_requires_processing_even_without_case_version_change(self):
        self.output()
        self.service.assets.put_file(case_id=self.case.case_id,file_id="new",file_name="new.txt",media_type="text/plain",document_type="other",content=b"new")
        result=self.service.prepare_case_continuation(self.request(snapshot=False))
        self.assertEqual(result.case_version,1)
        self.assertEqual(result.data.unlinked_files[0]["file_id"],"new")
        self.assertTrue(result.data.outputs[0]["needs_review"])
        self.assertIn("parse_documents",result.data.suggested_interfaces)

    def test_new_message_is_pending_interpretation_not_confirmed_fact(self):
        self.output()
        self.advance(lambda raw:raw["statements"].append(dict(message_id="new",actor_id="synthetic_user",text="收到部分款项",created_at=raw["updated_at"])))
        result=self.service.prepare_case_continuation(self.request(snapshot=False,since_version=1))
        self.assertFalse(result.data.changes_since.substantive_changed)
        self.assertTrue(result.data.outputs[0]["needs_review"])
        self.assertIn("CaseIntakeService.prepare_review",result.data.suggested_interfaces)
        self.assertEqual(len(result.data.case_snapshot.facts),len(self.case.facts))

    def test_restart_restores_files_outputs_and_history(self):
        self.output();self.advance()
        restarted=CaseService(SQLiteCaseRepository(Path(self.temp.name)/"cases.db"))
        result=restarted.prepare_case_continuation(self.request(snapshot=False,since_version=1))
        self.assertEqual(result.data.case_snapshot.case_version,2)
        self.assertEqual(result.data.outputs[0]["output_id"],"candidate")
        self.assertEqual(len(restarted.get_case_history(self.request(snapshot=False)).data.items),2)

    def test_wrong_version_and_reversed_diff_are_errors(self):
        self.advance()
        request=self.request(snapshot=False);request["case_version"]=1
        self.assertEqual(self.service.prepare_case_continuation(request).error.code,"version_conflict")
        self.assertEqual(self.service.compare_case_versions(self.request(snapshot=False,from_version=2,to_version=1)).error.code,"invalid_request")

    def test_expired_and_unknown_case_read(self):
        request=self.request(snapshot=False)
        request["context"]["deadline_at"]=datetime.now(timezone.utc)-timedelta(seconds=1)
        self.assertEqual(self.service.get_case_history(request).error.code,"timeout")
        request=self.request(snapshot=False);request["context"]["case_id"]="missing"
        self.assertEqual(self.service.prepare_case_continuation(request).error.code,"not_found")

    def test_inventory_returns_latest_output_version_only(self):
        old=self.output()
        updated=old.model_copy(update={"output_version":2,"text":"second"})
        self.service.assets.save_output(updated)
        result=self.service.prepare_case_continuation(self.request(snapshot=False))
        self.assertEqual(len(result.data.outputs),1)
        self.assertEqual(result.data.outputs[0]["output_version"],2)
        self.assertEqual(self.service.assets.get_output(self.case.case_id,old.output_id,1)["output"],old)
