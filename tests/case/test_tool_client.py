"""Regressions at the shared model/evaluation boundary."""

import json
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.case import CaseService, CaseToolClient, SQLiteCaseRepository, ToolInvocation, load_development_case
from backend.tools.case_tools import get_case_tool_contracts
from backend.case.contracts import InvocationContext
from backend.agent.case_adapter import RepositoryCasePort


class CaseToolClientTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repo = SQLiteCaseRepository(Path(self.directory.name) / "cases.db")
        self.case = self.repo.create_case(load_development_case())
        self.service = CaseService(self.repo)
        self.client = CaseToolClient(self.service)

    def invocation(self, arguments=None, request_id="client-test", version=1, deadline=None):
        return ToolInvocation(
            context=InvocationContext(request_id=request_id, case_id=self.case.case_id,
                                      experiment_run_id="phase4-dev", deadline_at=deadline),
            case_version=version,
            arguments=arguments or {},
        )

    def test_evaluation_preserves_insufficient_and_correlation(self):
        result = self.client.evaluate("check_evidence", self.invocation())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["status"], "insufficient")
        self.assertEqual(result["request_id"], "client-test")
        self.assertEqual(result["meta"]["attempt_count"], 1)
        self.assertGreaterEqual(result["meta"]["duration_ms"], 0)

    def test_execution_does_not_import_agent_tool_definitions(self):
        import builtins
        original_import = builtins.__import__

        def business_import(name, *args, **kwargs):
            if name == "backend.tools" or name.startswith("backend.tools."):
                raise AssertionError("business execution imported Agent tools")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=business_import):
            result = self.client.invoke("check_evidence", self.invocation())
        self.assertEqual(result.status, "insufficient")
        self.assertIsNotNone(result.data)

    def test_arguments_cannot_inject_trusted_context_or_snapshot(self):
        for field in ["case_id", "case_version", "context", "case_snapshot"]:
            with self.subTest(field=field):
                result = self.client.invoke("check_evidence", self.invocation({field: "forged"}))
                self.assertEqual(result.error.code, "invalid_request")
        self.assertEqual(self.repo.get_case(self.case.case_id), self.case)

    def test_tool_update_cannot_insert_user_messages(self):
        result = self.client.invoke("update_case", self.invocation({
            "expected_case_version": 1, "change_reason": "test",
            "changes": {"upsert_statements": []},
        }))
        self.assertEqual(result.error.code, "invalid_request")
        self.assertEqual(self.repo.get_case(self.case.case_id).case_version, 1)

    def test_update_replay_uses_same_persisted_response(self):
        call = self.invocation({
            "expected_case_version": 1, "change_reason": "record unconfirmed user statement",
            "changes": {"upsert_facts": [{
                "fact_id": "f_client_note", "field": "client_note", "value_type": "text",
                "value": "合成补充信息", "provenance": "user_statement",
                "confirmation_status": "unconfirmed", "statement_refs": ["msg_001"],
            }]},
        })
        first = self.client.invoke("update_case", call)
        replay = self.client.invoke("update_case", call)
        self.assertEqual(first.status, "ok", first.error)
        self.assertEqual(first, replay)
        self.assertEqual(self.repo.get_case(self.case.case_id).case_version, 2)

    def test_stale_version_is_not_silently_replaced(self):
        RepositoryCasePort(self.repo).set_stage(self.case.case_id, "route_selection")
        result = self.client.evaluate("check_evidence", self.invocation())
        self.assertEqual(result["error"]["code"], "version_conflict")
        self.assertEqual(result["error"]["details"]["case_version"], 2)

    def test_stage_rules_apply_to_fixed_calls(self):
        result = self.client.invoke("build_evidence_directory", self.invocation())
        self.assertEqual(result.error.code, "invalid_request")
        self.assertEqual(result.error.details["reason"], "stage_not_allowed")

    def test_unknown_tool_and_saved_output_have_explicit_errors(self):
        self.assertEqual(self.client.invoke("not_a_tool", self.invocation()).error.code, "not_found")
        result = self.client.invoke("verify_output", self.invocation({"output_id": "missing"}))
        self.assertEqual(result.error.code, "not_found")

    def test_expired_call_does_not_execute_service(self):
        def unexpected_call(request):
            raise AssertionError("expired request reached service")
        self.service.check_evidence = unexpected_call
        result = self.client.invoke("check_evidence", self.invocation(
            deadline=datetime.now(timezone.utc) - timedelta(seconds=1)))
        self.assertEqual(result.error.code, "timeout")

    def test_explicit_handler_preserves_validation_context_and_activity(self):
        handler = Mock(wraps=self.service.check_evidence)
        # The Agent path supplies the method explicitly; no second name lookup.
        with patch.object(self.service, "check_evidence", side_effect=AssertionError("unexpected named dispatch")):
            result, receipt = self.client.invoke_recorded(
                "check_evidence", self.invocation(), handler=handler,
                run_id="explicit-run", step_id="2",
            )
        self.assertEqual(result.status, "insufficient")
        handler.assert_called_once()
        request = handler.call_args.args[0]
        self.assertEqual(request.context.case_id, self.case.case_id)
        self.assertEqual(request.case_snapshot, self.case)
        self.assertTrue(receipt["recorded"])

        for name, call in (
            ("check_evidence", self.invocation({"case_snapshot": {}})),
            ("check_evidence", self.invocation(version=99)),
            ("check_evidence", self.invocation(deadline=datetime.now(timezone.utc) - timedelta(seconds=1))),
            ("build_evidence_directory", self.invocation()),
            ("unknown_tool", self.invocation()),
        ):
            with self.subTest(name=name, call=call):
                handler.reset_mock()
                result, _ = self.client.invoke_recorded(name, call, handler=handler)
                self.assertEqual(result.status, "error")
                handler.assert_not_called()

    def test_service_failure_is_reported_without_exception_input(self):
        def fail(request):
            raise OSError("private material content")
        self.service.check_evidence = fail
        result = self.client.evaluate("check_evidence", self.invocation())
        self.assertEqual(result["error"]["code"], "tool_error")
        self.assertNotIn("private material content", json.dumps(result))

    def test_contracts_have_ten_schemas_and_no_user_message_insertion(self):
        from jsonschema import Draft202012Validator
        contracts = get_case_tool_contracts()
        self.assertEqual(len(contracts), 10)
        for name, spec in contracts.items():
            with self.subTest(name=name):
                Draft202012Validator.check_schema(spec["parameters"])
                self.assertNotIn("context", spec["parameters"]["properties"])
                self.assertEqual(spec["max_retries"], 0)
        self.assertNotIn("upsert_statements", json.dumps(contracts["update_case"]["parameters"]))
        self.assertTrue(contracts["build_evidence_directory"]["side_effects"])
        from backend.tools.case_contracts import ARGUMENT_MODELS, OPERATION_STAGES
        self.assertEqual(set(contracts), set(ARGUMENT_MODELS))
        for name, spec in contracts.items():
            self.assertEqual(spec["stages"], list(OPERATION_STAGES[name]))
            self.assertEqual(spec["parameters"], ARGUMENT_MODELS[name].model_json_schema())

    def test_invalid_case_version_returns_structured_error(self):
        payload = self.invocation().model_dump(mode="json")
        payload["case_version"] = True
        result = self.client.evaluate("check_evidence", payload)
        self.assertEqual(result["error"]["code"], "invalid_request")

    def test_catalog_imports_in_either_order_without_agent_runtime(self):
        # Both business-only callers and Agent assembly can be the first importer.
        for first in ("backend.tools.case_client", "backend.tools.case_tools"):
            with self.subTest(first=first):
                code = (
                    f"import {first}\n"
                    "from backend.case import get_contract_manifest, get_case_tool_contracts\n"
                    "from backend.tools.case_tools import get_case_tool_contracts as catalog\n"
                    "assert get_contract_manifest()['tool_operations'] == catalog() == get_case_tool_contracts()\n"
                    "import sys\n"
                    "assert not any(n == 'backend.agent' or n.startswith('backend.agent.') "
                    "for n in sys.modules)\n"
                )
                result = subprocess.run(
                    [sys.executable, "-B", "-c", code],
                    cwd=Path(__file__).resolve().parents[2],
                    capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_export_and_registry_changes_do_not_mutate_shared_tool_definitions(self):
        from tests.native_tools import register_case_tools
        from tests.native_tools import NativeTools

        original = get_case_tool_contracts()
        exported = get_case_tool_contracts()
        exported["check_evidence"]["stages"].clear()
        exported["parse_documents"]["parameters"]["properties"].clear()
        with NativeTools() as first, NativeTools() as second:
            register_case_tools(first, self.service)
            register_case_tools(second, self.service)
            first._tools["check_evidence"].metadata["civra"]["stages"].clear()
            first._tools["parse_documents"].args_schema["properties"].clear()
            self.assertIn("consultation", second.get_spec("check_evidence").stages)
            self.assertIn("file_ids", second.get_spec("parse_documents").parameters["properties"])
        self.assertEqual(get_case_tool_contracts(), original)
