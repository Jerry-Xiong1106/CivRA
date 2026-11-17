"""Contract-boundary tests, independent of any model, frontend or legal assessment."""

import json
import unittest
from copy import deepcopy
from decimal import Decimal

from pydantic import ValidationError

from backend.case import (
    get_contract_manifest, load_development_case, runtime_case_view,
    to_evaluation_result, to_runtime_tool_result, validate_case_snapshot,
)
from scripts.case.fixtures import DEVELOPMENT_CASE
from backend.case.materials.contracts import CheckEvidenceData, CheckEvidenceRequest
from backend.case.drafting.contracts import DraftDocumentRequest
from backend.case.contracts import InvocationContext
from backend.case.intake.contracts import UpdateCaseRequest
from backend.case.review.contracts import VerifyOutputRequest
from backend.case.models import (
    BusinessError, BusinessOutput, BusinessResult, CaseSnapshot, MaterialRef, Money, SourcePointer,
)


class CaseContractsTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = load_development_case()
        self.payload = self.snapshot.model_dump(mode="json")
        self.context = InvocationContext(request_id="test-001", case_id=self.snapshot.case_id,
                                         experiment_run_id="test-only", deadline_at=None)

    def test_fixture_roundtrip_checks_real_materials(self):
        self.assertEqual(self.snapshot, CaseSnapshot.model_validate_json(self.snapshot.model_dump_json()))
        self.assertTrue(self.snapshot.synthetic)
        self.assertEqual(len(self.snapshot.evidence), 3)

    def test_conflicts_and_duplicates_remain_valid_inputs(self):
        dates = {fact.value for fact in self.snapshot.facts if fact.field == "accident_date"}
        self.assertEqual(dates, {"2026-05-12", "2026-05-13"})
        a, b = self.snapshot.evidence[1:]
        self.assertEqual(a.content_sha256, b.content_sha256)
        self.assertIsNone(b.duplicate_of)
        self.assertEqual(validate_case_snapshot(self.payload).status, "ok")

    def test_money_requires_decimal_string_and_currency(self):
        self.assertEqual(Money(amount="3000.00", currency="CNY").decimal, Decimal("3000.00"))
        for amount in [3000.0, 3000, True, "NaN", "Infinity", "3e3", "-1", " 3.00"]:
            with self.subTest(amount=amount), self.assertRaises(ValidationError):
                Money(amount=amount, currency="CNY")
        with self.assertRaises(ValidationError):
            Money(amount="3.00", currency="cny")

    def test_money_fact_rejects_float(self):
        self.payload["facts"][3]["value"]["amount"] = 3000.0
        self.assertEqual(validate_case_snapshot(self.payload).error.code, "invalid_request")

    def test_confirmed_fact_needs_a_confirmation(self):
        self.payload["facts"][0]["confirmation_status"] = "confirmed"
        self.assertEqual(validate_case_snapshot(self.payload).status, "error")

    def test_confirming_actor_must_match_statement(self):
        self.payload["facts"][2]["confirmation"]["confirmed_by"] = "someone_else"
        self.assertEqual(validate_case_snapshot(self.payload).status, "error")

    def test_unknown_fact_value_is_null(self):
        self.payload["facts"][0]["value"] = None
        self.assertEqual(validate_case_snapshot(self.payload).status, "ok")
        self.payload["facts"][0]["confirmation_status"] = "confirmed"
        self.assertEqual(validate_case_snapshot(self.payload).status, "error")

    def test_date_format_and_calendar_are_checked(self):
        for value in ["2026-02-30", "20260512", "2026-5-12", 20260512]:
            payload = deepcopy(self.payload)
            payload["facts"][0]["value"] = value
            with self.subTest(value=value):
                self.assertEqual(validate_case_snapshot(payload).status, "error")

    def test_utc_serialization_and_naive_timestamp_rejection(self):
        payload = deepcopy(self.payload)
        payload["created_at"] = "2026-11-01T16:00:00+08:00"
        self.assertEqual(CaseSnapshot.model_validate(payload).model_dump(mode="json")["created_at"], "2026-11-01T08:00:00Z")
        payload["created_at"] = "2026-11-01T08:00:00"
        self.assertEqual(validate_case_snapshot(payload).status, "error")

    def test_invalid_material_spans_and_missing_location(self):
        for extra in [{}, {"span_start": 1}, {"span_start": 3, "span_end": 2}, {"page": 0}]:
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                MaterialRef(file_id="file-1", file_version="1", **extra)

    def test_duplicate_ids_are_rejected(self):
        self.payload["facts"].append(deepcopy(self.payload["facts"][0]))
        self.assertEqual(validate_case_snapshot(self.payload).status, "error")

    def test_dangling_reference_fixture_is_rejected(self):
        payload = json.loads((DEVELOPMENT_CASE.parent / "invalid_case_snapshot.json").read_text(encoding="utf-8"))
        result = validate_case_snapshot(payload)
        self.assertEqual(result.error.code, "invalid_request")
        self.assertNotIn("input", result.error.details["issues"][0])

    def test_fact_location_must_match_its_evidence(self):
        self.payload["facts"][1]["material_refs"][0] = self.payload["evidence"][1]["material_refs"][0]
        self.assertEqual(validate_case_snapshot(self.payload).status, "error")

    def test_duplicate_graph_dangling_and_cycles_are_rejected(self):
        for values in [("e_missing", None), ("e_invoice_copy", "e_invoice")]:
            payload = deepcopy(self.payload)
            payload["evidence"][1]["duplicate_of"], payload["evidence"][2]["duplicate_of"] = values
            self.assertEqual(validate_case_snapshot(payload).status, "error")

    def test_request_cannot_mix_case_ids_or_versions(self):
        for case_id, version in [("other-case", 1), (self.snapshot.case_id, 2)]:
            context = self.context.model_copy(update={"case_id": case_id})
            with self.subTest(case_id=case_id, version=version), self.assertRaises(ValidationError):
                CheckEvidenceRequest(context=context, case_version=version, case_snapshot=self.snapshot)

    def test_stages_require_consistent_user_route(self):
        self.payload["stage"] = "litigation"
        self.assertEqual(validate_case_snapshot(self.payload).status, "error")
        self.payload["selected_route"] = "litigation"
        self.assertEqual(validate_case_snapshot(self.payload).status, "ok")

    def test_draft_requires_litigation_and_only_confirmed_facts(self):
        fields = dict(context=self.context, case_version=1, confirmed_fact_ids=["f_claimant"],
                      route="litigation", template_id="synthetic-template", template_version="1", source_refs=[])
        with self.assertRaises(ValidationError):
            DraftDocumentRequest(case_snapshot=self.snapshot, **fields)
        payload = deepcopy(self.payload)
        payload.update(stage="litigation", selected_route="litigation")
        selected = CaseSnapshot.model_validate(payload)
        DraftDocumentRequest(case_snapshot=selected, **fields)
        fields["confirmed_fact_ids"] = ["f_date_document"]
        with self.assertRaises(ValidationError):
            DraftDocumentRequest(case_snapshot=selected, **fields)

    def test_update_requires_expected_version_and_nonempty_changes(self):
        fields = dict(context=self.context, expected_case_version=1, changes={}, change_reason="用户补充")
        with self.assertRaises(ValidationError):
            UpdateCaseRequest(**fields)
        fields["changes"] = {"upsert_facts": [self.payload["facts"][0]]}
        self.assertEqual(UpdateCaseRequest(**fields).expected_case_version, 1)
        fields["expected_case_version"] = True
        with self.assertRaises(ValidationError):
            UpdateCaseRequest(**fields)

    def test_output_version_staleness_and_case_isolation(self):
        output = BusinessOutput.model_validate_json((DEVELOPMENT_CASE.parent / "example_output.json").read_text(encoding="utf-8"))
        self.assertFalse(output.is_stale_for(self.snapshot))
        newer = self.snapshot.model_copy(update={"case_version": 2})
        self.assertTrue(output.is_stale_for(newer))
        with self.assertRaises(ValueError):
            output.is_stale_for(self.snapshot.model_copy(update={"case_id": "another"}))
        with self.assertRaises(ValueError):
            output.model_copy(update={"case_version": 3}).is_stale_for(newer)
        VerifyOutputRequest(context=self.context, case_version=2, case_snapshot=newer,
                            candidate_output=output, cited_sources=[])

    def test_template_version_and_output_content_are_required(self):
        with self.assertRaises(ValidationError):
            BusinessOutput(output_id="out", output_version=1, case_id=self.snapshot.case_id,
                           case_version=1, output_type="document")

    def test_result_status_error_invariants(self):
        for status, data, error in [("ok", None, None), ("error", {}, None),
                                     ("ok", {}, BusinessError(code="tool_error", message="failed"))]:
            with self.subTest(status=status), self.assertRaises(ValidationError):
                BusinessResult(case_id=self.snapshot.case_id, case_version=1, status=status,
                               summary="test", data=data, error=error)

    def test_evaluation_preserves_business_insufficient(self):
        result = BusinessResult[CheckEvidenceData](case_id=self.snapshot.case_id, case_version=1,
            status="insufficient", summary="example only", data=CheckEvidenceData(
                missing_facts=[], conflicts=[], duplicates=[], suggested_questions=[]))
        wrapped = to_evaluation_result(result, self.context, duration_ms=7)
        self.assertEqual(wrapped["status"], "ok")
        self.assertEqual(wrapped["data"]["status"], "insufficient")
        self.assertEqual(wrapped["meta"]["attempt_count"], 1)
        self.assertEqual(to_runtime_tool_result(result)["status"], "insufficient")

    def test_error_adapters_preserve_version_conflict_code(self):
        result = BusinessResult(case_id=self.snapshot.case_id, case_version=1, status="error",
            summary="stale version", data=None, error=BusinessError(code="version_conflict", message="stale version"))
        evaluation = to_evaluation_result(result, self.context, duration_ms=1)
        self.assertEqual(evaluation["status"], "error")
        self.assertIsNone(evaluation["data"])
        self.assertEqual(evaluation["error"]["code"], "version_conflict")
        runtime = to_runtime_tool_result(result)
        self.assertEqual(runtime["error"]["code"], "INVALID_ARGS")
        self.assertEqual(runtime["error"]["details"]["business_code"], "version_conflict")

    def test_source_resolver_must_preserve_source_version(self):
        result = validate_case_snapshot(self.payload)
        result.source_refs = [SourcePointer(source_id="example-source", source_version="v1")]
        with self.assertRaises(ValueError):
            to_runtime_tool_result(result)
        source = {"source_id": "example-source", "version": "v2", "source_type": "template",
                  "title": "Synthetic source", "locator": "paragraph:1"}
        with self.assertRaises(ValueError):
            to_runtime_tool_result(result, source_resolver=lambda pointer: source)
        source["version"] = "v1"
        self.assertEqual(to_runtime_tool_result(result, source_resolver=lambda pointer: source)["data"]["matches"][0]["source_ref"]["source_version"], "v1")

    def test_runtime_case_view_and_contract_manifest(self):
        view = runtime_case_view(self.snapshot)
        self.assertEqual(set(view), {"case_id", "case_version", "stage", "summary"})
        self.assertEqual(view["summary"]["facts"][0]["confirmation_status"], "unconfirmed")
        manifest = get_contract_manifest()
        self.assertEqual(len(manifest["business_operations"]), 10)
        implemented = {key for key, value in manifest["business_operations"].items() if value == "implemented"}
        self.assertEqual(implemented, set(manifest["business_operations"]))
        self.assertIn("check_evidence_result", manifest["schemas"])
        json.dumps(manifest)

    def test_unknown_fields_and_invalid_versions_are_rejected(self):
        self.payload["hidden_gold"] = {"do_not_import": True}
        self.assertEqual(validate_case_snapshot(self.payload).status, "error")
        del self.payload["hidden_gold"]
        for version in [True, 0, 1.0, "1"]:
            self.payload["case_version"] = version
            self.assertEqual(validate_case_snapshot(self.payload).status, "error")


if __name__ == "__main__":
    unittest.main()
