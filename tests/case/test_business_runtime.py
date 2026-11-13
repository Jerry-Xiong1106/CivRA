"""Integration checks requiring Member 4's actual ToolRegistry and AgentRunner."""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.case import CaseService, SQLiteCaseRepository, load_development_case

try:
    from backend.tools.registry import ToolRegistry
    from backend.common.schemas import ToolContext
except ModuleNotFoundError:
    ToolRegistry = None


@unittest.skipIf(ToolRegistry is None, "requires Member 4 runtime in the integration checkout")
class BusinessRuntimeTests(unittest.TestCase):
    def setUp(self):
        from backend.tools.case_tools import register_case_tools
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repo = SQLiteCaseRepository(Path(self.directory.name) / "cases.sqlite3")
        self.case = self.repo.create_case(load_development_case())
        self.registry = register_case_tools(ToolRegistry(), CaseService(self.repo))

    def context(self, stage="consultation", version=1):
        return ToolContext(case_id=self.case.case_id, run_id="run_test", step_id=1,
            case_version=version, deadline=datetime.now(timezone.utc)+timedelta(seconds=30), stage=stage)

    def test_real_tool_result_contains_actual_findings_and_material_sources(self):
        execution = self.registry.execute("check_evidence", {}, self.context())
        self.assertEqual(execution.result.status, "insufficient")
        self.assertEqual(len(execution.result.data["data"]["conflicts"]), 1)
        self.assertTrue(any(match["source_ref"]["source_type"] == "user_material" for match in execution.result.data["matches"]))
        activity = CaseService(self.repo).get_tool_activity({
            "context": {"case_id":self.case.case_id,"request_id":"activity-check","experiment_run_id":None,"deadline_at":None},
            "case_version":1})
        self.assertEqual([e['run_id'] for e in activity.data.events],['run_test','run_test'])
        self.assertEqual([e['step_id'] for e in activity.data.events],['1','1'])

    def test_model_cannot_insert_fabricated_user_messages(self):
        args = {"expected_case_version": 1, "changes": {"upsert_statements": []}, "change_reason": "test"}
        result = self.registry.execute("update_case", args, self.context()).result
        self.assertEqual(result.status, "error")
        self.assertEqual(result.error.code, "INVALID_ARGS")
        self.assertEqual(self.repo.get_case(self.case.case_id).case_version, 1)

    def test_empty_update_is_invalid_not_internal_error(self):
        args = {"expected_case_version": 1, "changes": {}, "change_reason": "test"}
        result = self.registry.execute("update_case", args, self.context()).result
        self.assertEqual(result.error.code, "INVALID_ARGS")

    def test_same_tool_attempt_can_be_replayed_without_second_write(self):
        args = {"expected_case_version": 1, "change_reason": "演示用户陈述",
            "changes": {"upsert_facts": [{"fact_id": "f_insurance_tool", "field": "insurance", "value_type": "text",
                "value": "待核实具体保险关系", "provenance": "user_statement", "confirmation_status": "unconfirmed",
                "statement_refs": ["msg_001"]}]}}
        context = self.context()
        first = self.registry.execute("update_case", args, context).result
        replay = self.registry.execute("update_case", args, context).result
        self.assertEqual(first.status, "ok")
        self.assertEqual(first, replay)
        self.assertEqual(self.repo.get_case(self.case.case_id).case_version, 2)

    def test_stage_gating_prevents_litigation_directory(self):
        result = self.registry.execute("build_evidence_directory", {}, self.context()).result
        self.assertEqual(result.error.code, "NOT_ALLOWED")

    def test_registry_rejects_stale_case_context(self):
        from backend.case.runtime import RepositoryCasePort
        RepositoryCasePort(self.repo).set_stage(self.case.case_id, "route_selection")
        result = self.registry.execute("check_evidence", {}, self.context()).result
        self.assertEqual(result.error.details["business_code"], "version_conflict")
        self.assertEqual(result.case_version, 2)

    def test_real_agent_asks_updates_resumes_and_builds_directory(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/case"))
        from demo_agent_business import run_agent_demo
        with TemporaryDirectory() as directory:
            result = run_agent_demo(directory)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["case_version"], 4)
        self.assertEqual(len(result["questions_from_real_report"]), 4)
        self.assertEqual([(step["name"],step["status"]) for step in result["tool_steps"]], [
            ("check_evidence", "insufficient"), ("update_case", "ok"),
            ("check_evidence", "ok"), ("build_evidence_directory", "ok")])
        self.assertEqual(result["history_versions"], [1, 2, 3, 4])


if __name__ == "__main__":
    unittest.main()
