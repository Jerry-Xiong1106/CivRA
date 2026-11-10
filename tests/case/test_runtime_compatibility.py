"""Run these checks after combining Member 4's branch in an integration checkout."""

import unittest
from tempfile import TemporaryDirectory

from backend.case import load_development_case, runtime_case_view, to_runtime_tool_result, validate_case_snapshot

try:
    from backend.agent.case_port import CaseSnapshot as RuntimeCaseSnapshot
    from backend.common.schemas import ToolResult
except ModuleNotFoundError:
    RuntimeCaseSnapshot = None
    ToolResult = None


@unittest.skipIf(ToolResult is None, "Member 4 runtime is not on this branch; run in the combined checkout")
class RuntimeCompatibilityTests(unittest.TestCase):
    def test_case_view_matches_actual_runtime_model(self):
        snapshot = load_development_case()
        runtime = RuntimeCaseSnapshot.model_validate(runtime_case_view(snapshot))
        self.assertEqual(runtime.case_version, snapshot.case_version)
        self.assertEqual(runtime.summary["evidence"][0]["evidence_id"], snapshot.evidence[0].evidence_id)

    def test_business_result_matches_actual_tool_result(self):
        snapshot = load_development_case()
        result = validate_case_snapshot(snapshot.model_dump(mode="json"))
        runtime_result = to_runtime_tool_result(result, result_type=ToolResult)
        self.assertIsInstance(runtime_result, ToolResult)
        self.assertEqual(runtime_result.data["data"]["case_id"], snapshot.case_id)

    def test_registered_contract_validation_tool_runs_in_agent(self):
        from backend.agent.llm import ScriptedLLM, tool_call
        from backend.agent.runner import AgentRunner
        from backend.agent.store import RunStore
        from backend.common.schemas import ToolSpec
        from backend.tools.registry import ToolRegistry

        snapshot = load_development_case()

        class ReadOnlyCasePort:
            def get_snapshot(self, case_id):
                if case_id != snapshot.case_id:
                    raise KeyError(case_id)
                return RuntimeCaseSnapshot.model_validate(runtime_case_view(snapshot))

            def set_stage(self, case_id, stage):
                raise AssertionError("contract validation must not change the user's route")

        registry = ToolRegistry()
        spec = ToolSpec(name="validate_case_snapshot", description="Validate case structure only",
                        parameters={"type": "object", "additionalProperties": False}, owner="member3")

        @registry.tool(spec)
        def validate_tool(args, context):
            self.assertEqual(context.case_id, snapshot.case_id)
            return to_runtime_tool_result(validate_case_snapshot(snapshot.model_dump(mode="json")),
                                          result_type=ToolResult)

        llm = ScriptedLLM([tool_call("validate_case_snapshot"), tool_call("finish", answer="Structure validated")])
        with TemporaryDirectory() as directory:
            runner = AgentRunner(llm, registry, ReadOnlyCasePort(), RunStore(directory))
            info = runner.start_run(snapshot.case_id, "Validate the development case structure")
            self.assertEqual(info.status, "completed")
            self.assertEqual(info.case_version, 1)
            step = next(item for item in runner.get_trace(info.run_id) if item.tool_name == "validate_case_snapshot")
            self.assertEqual(step.status, "ok")


if __name__ == "__main__":
    unittest.main()
