"""Shared-entry-point parity against the frozen real Runtime integration."""

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.case import CaseService, SQLiteCaseRepository, load_development_case

try:
    from backend.common.schemas import ToolSpec
    from backend.tools.registry import ToolRegistry
except ModuleNotFoundError:
    ToolRegistry = None


@unittest.skipIf(ToolRegistry is None, "requires the saved Member 4 runtime integration copy")
class SharedRuntimeTests(unittest.TestCase):
    def test_agent_and_fixed_workflow_produce_equivalent_business_outputs(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/case"))
        from demo_shared_tools import run_shared_demo
        with TemporaryDirectory() as directory:
            result = run_shared_demo(directory)
        self.assertEqual(result["agent_status"], "completed")
        self.assertTrue(result["business_outputs_equivalent"])
        self.assertTrue(result["case_snapshots_equivalent"])
        self.assertEqual(len(result["tools_exercised"]), 10)
        self.assertEqual(result["tool_steps_per_mode"], 11)

    def test_duplicate_registration_preserves_the_existing_registry(self):
        from backend.tools.case_tools import register_case_tools
        with TemporaryDirectory() as directory:
            repo = SQLiteCaseRepository(Path(directory) / "cases.db")
            repo.create_case(load_development_case())
            registry = ToolRegistry()
            registry.register(ToolSpec(name="verify_output", owner="test",
                                      description="existing tool", parameters={"type": "object"}),
                              lambda args, context: None)
            with self.assertRaises(ValueError):
                register_case_tools(registry, CaseService(repo))
            self.assertEqual(registry.names(), ["verify_output"])

    def test_source_adapter_failure_preserves_saved_output_references(self):
        import json
        from datetime import datetime, timedelta, timezone
        from backend.common.schemas import ToolContext
        from backend.case.runtime import RepositoryCasePort
        from backend.tools.case_tools import register_case_tools
        with TemporaryDirectory() as directory:
            repo = SQLiteCaseRepository(Path(directory) / "cases.db")
            case = repo.create_case(load_development_case())
            RepositoryCasePort(repo).set_stage(case.case_id, "litigation")
            service = CaseService(repo, source_resolver=lambda pointer: {
                "source_id": pointer.source_id, "version": pointer.source_version,
                "source_type": "statute", "title": "合成来源", "locator": "第一条",
                "text": "仅用于开发测试的合成来源。", "excerpt": "仅用于开发测试的合成来源。",
                "effective_from": "2020-01-01", "url": None,
            })
            def unavailable_source(pointer):
                raise OSError("private upstream diagnostic")
            registry = register_case_tools(ToolRegistry(), service, source_resolver=unavailable_source)
            context = ToolContext(case_id=case.case_id, case_version=2, run_id="failure-test", step_id=1,
                                  stage="litigation", deadline=datetime.now(timezone.utc) + timedelta(seconds=30))
            arguments = {
                "route": "litigation", "confirmed_fact_ids": ["f_claimant"],
                "template_id": "cn_complaint_traffic_accident", "template_version": "civra-fields-v1",
                "source_refs": [{"source_id": "synthetic-source", "source_version": "1"}],
            }
            result = registry.execute("draft_document", arguments, context).result
            self.assertEqual(result.error.code, "INTERNAL")
            self.assertNotIn("private upstream diagnostic", json.dumps(result.model_dump()))
            output_refs = result.error.details["output_refs"]
            self.assertEqual(len(output_refs), 1)
            self.assertEqual(service.assets.get_output(case.case_id, output_refs[0])["output"].case_version, 2)
