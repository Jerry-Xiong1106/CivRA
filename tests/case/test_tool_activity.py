from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
import json
import unittest
from backend.case import CaseService, SQLiteCaseRepository
from scripts.case.fixtures import load_development_case
from backend.tools.case_client import CaseToolClient
from backend.case.models import CaseSnapshot
from features10_13_support import FeatureFixture


class ActivityTests(FeatureFixture,unittest.TestCase):
    def call(self,name="activity",arguments=None):
        return dict(context=self.context(name),case_version=self.repo.get_case(self.case.case_id).case_version,arguments=arguments or {})

    def events(self,**extra):
        return self.service.get_tool_activity(self.request(snapshot=False,**extra))

    def test_start_and_finish_have_actual_status_versions_and_runtime_ids(self):
        result,receipt=CaseToolClient(self.service).invoke_recorded("check_evidence",self.call(),run_id="run1",step_id="step1")
        self.assertTrue(receipt["recorded"])
        events=self.events().data.events
        self.assertEqual([e["phase"] for e in events],["started","finished"])
        self.assertEqual(events[1]["status"],result.status)
        self.assertEqual(events[1]["case_version"],1)
        self.assertEqual(events[1]["run_id"],"run1")
        self.assertGreaterEqual(events[1]["duration_ms"],0)
        self.assertIsNone(events[1]["model_usage"])

    def test_retries_are_separate_attempts_with_same_request(self):
        client=CaseToolClient(self.service)
        call=self.call(arguments=dict(chart_type="timeline"))
        first=client.invoke("build_visualization",call);second=client.invoke("build_visualization",call)
        self.assertEqual(first,second)
        events=self.events().data.events
        self.assertEqual(len(events),4)
        self.assertEqual(len({e["attempt_id"] for e in events}),2)
        self.assertEqual(len({e["request_id"] for e in events}),1)
        self.assertEqual(events[-1]["output_refs"],first.output_refs)

    def test_failures_record_code_without_raw_arguments(self):
        client=CaseToolClient(self.service)
        result=client.invoke("check_evidence",self.call(arguments=dict(private_text="PRIVATE_DO_NOT_LOG")))
        self.assertEqual(result.status,"error")
        events=self.events().data.events
        self.assertEqual(events[-1]["error"]["code"],"invalid_request")
        self.assertNotIn("PRIVATE_DO_NOT_LOG",json.dumps(events))
        self.assertNotIn("arguments",events[0])

    def test_start_storage_failure_prevents_operation(self):
        client=CaseToolClient(self.service)
        with patch.object(self.service.activity,"begin",side_effect=OSError),patch.object(client,"_invoke") as execute:
            result,receipt=client.invoke_recorded("check_evidence",self.call())
            execute.assert_not_called()
        self.assertEqual(result.error.details,{"operation_started":False})
        self.assertFalse(receipt["operation_started"])

    def test_finish_failure_preserves_committed_business_result_and_start(self):
        client=CaseToolClient(self.service)
        with patch.object(self.service.activity,"finish",side_effect=OSError):
            result,receipt=client.invoke_recorded("build_visualization",self.call(arguments=dict(chart_type="timeline")))
        self.assertEqual(result.status,"ok",result.error)
        self.assertTrue(receipt["recording_failed"])
        self.assertTrue(self.service.assets.get_output(self.case.case_id,result.output_refs[0]))
        self.assertEqual([e["phase"] for e in self.events().data.events],["started"])

    def test_pagination_boundary_excludes_later_events(self):
        client=CaseToolClient(self.service);client.invoke("check_evidence",self.call("first"))
        page=self.events(limit=1)
        client.invoke("check_evidence",self.call("second"))
        later=self.events(after_sequence=page.data.next_after_sequence,through_sequence=page.data.through_sequence)
        self.assertEqual(len(later.data.events),1)
        self.assertEqual(later.data.events[0]["request_id"],"first")

    def test_restart_retains_activity_and_does_not_mix_cases(self):
        CaseToolClient(self.service).invoke("check_evidence",self.call())
        self.service=CaseService(SQLiteCaseRepository(Path(self.temp.name)/"cases.db"))
        self.assertEqual(len(self.events().data.events),2)
        other=load_development_case().model_dump(mode="json");other["case_id"]="another"
        self.repo.create_case(CaseSnapshot.model_validate(other))
        request=self.request(snapshot=False);request["context"]["case_id"]="another"
        self.assertEqual(self.service.get_tool_activity(request).data.events,[])

    def test_parallel_calls_keep_distinct_attempts_and_monotonic_events(self):
        client=CaseToolClient(self.service)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda n:client.invoke("check_evidence",self.call(f"parallel{n}")),range(6)))
        self.assertTrue(all(r.status!="error" for r in results))
        events=self.events().data.events
        self.assertEqual(len(events),12)
        self.assertEqual(len({e["attempt_id"] for e in events}),6)
        self.assertEqual([e["sequence"] for e in events],sorted(e["sequence"] for e in events))

    def test_unknown_case_cannot_create_trace_rows(self):
        call=self.call();call["context"]["case_id"]="missing"
        result,receipt=CaseToolClient(self.service).invoke_recorded("check_evidence",call)
        self.assertEqual(result.error.code,"not_found")
        self.assertFalse(receipt["recorded"])
        self.assertEqual(self.events().data.events,[])

    def test_argument_ids_and_output_refs_can_be_queried(self):
        result=CaseToolClient(self.service).invoke("build_visualization",self.call(arguments=dict(chart_type="timeline")))
        events=self.events().data.events
        self.assertEqual(events[0]["input_refs"],{"chart_type":"timeline"})
        self.assertEqual(events[1]["output_refs"],result.output_refs)
