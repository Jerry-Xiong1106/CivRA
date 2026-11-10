from copy import deepcopy
import hashlib
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from backend.case.models import BusinessOutput, Finding, OutputLink, SourcePointer
from backend.case.runtime import RepositoryCasePort
from features10_13_support import FeatureFixture


class VerificationCorrectionTests(FeatureFixture,unittest.TestCase):
    def verify(self,output,request_id="review",refs=None):
        return self.service.verify_output(self.request(request_id,candidate_output=output,cited_sources=refs or output.source_refs))

    def test_custom_gateway_unknown_references_fail_closed(self):
        self.service.verifier.semantic_gateway=SimpleNamespace(review=lambda **_:([
            dict(finding_id="x",category="conflict",severity="warning",message="bad",fact_ids=["invented"])],None))
        result=self.verify(self.output())
        self.assertEqual(result.status,"insufficient")
        self.assertIn("semantic_review_failed",result.data.checks_not_performed)
        self.assertEqual(result.data.disposition,"needs_correction")
        self.assertTrue(result.data.correction_tasks)

    def test_gateway_timeout_does_not_persist_a_review(self):
        def timeout(**_): raise TimeoutError()
        self.service.verifier.semantic_gateway=SimpleNamespace(review=timeout)
        result=self.verify(self.output())
        self.assertEqual(result.error.code,"timeout")
        self.assertEqual(len(self.service.assets.list_case_inventory(self.case.case_id)["outputs"]),1)

    def test_gateway_cannot_mutate_current_case_or_candidate(self):
        output=self.output();before=output.model_dump()
        def review(**kwargs):
            kwargs["output"].text="forged"
            kwargs["case_snapshot"].facts[2].value="forged"
            return [],None
        self.service.verifier.semantic_gateway=SimpleNamespace(review=review)
        result=self.verify(output)
        self.assertEqual(result.status,"ok")
        self.assertEqual(output.model_dump(),before)
        self.assertEqual(self.repo.get_case(self.case.case_id),self.case)

    def test_malformed_structured_data_is_findings_not_crash(self):
        result=self.verify(self.output(structured_data=dict(facts=9,claims="bad",field_rows=[None],source_cards=[None])))
        self.assertEqual(result.status,"insufficient",result.error)
        self.assertGreaterEqual(len(result.data.findings),4)

    def test_source_reader_network_failure_is_reported(self):
        pointer=SourcePointer(source_id="law",source_version="1")
        self.service.external_source_resolver=lambda _:(_ for _ in ()).throw(OSError("offline"))
        result=self.verify(self.output(source_refs=[pointer]))
        self.assertEqual(result.status,"insufficient")
        self.assertEqual(result.data.source_checks[0]["state"],"rejected")

    def test_changed_quote_or_hash_is_detected(self):
        pointer=SourcePointer(source_id="law",source_version="1")
        self.service.external_source_resolver=lambda _:dict(source_id="law",version="1",source_type="law",title="测试",locator="第1条",text="合法原文",effective_from="2020-01-01")
        card=dict(source_ref=pointer.model_dump(),quote="伪造",quote_start=0,quote_end=2,text_sha256=hashlib.sha256("合法原文".encode()).hexdigest())
        result=self.verify(self.output(source_refs=[pointer],structured_data=dict(source_cards=[card])))
        self.assertTrue(any(f.output_location=="structured_data.source_cards" for f in result.data.findings))

    def test_draft_field_and_export_manifest_tampering_detected(self):
        RepositoryCasePort(self.repo).set_stage(self.case.case_id,"litigation")
        draft=self.service.draft_document(self.request("draft",route="litigation",confirmed_fact_ids=["f_claimant"],
            template_id="cn_complaint_traffic_accident",template_version="civra-fields-v2",source_refs=[]))
        forged=draft.data.model_copy(deep=True)
        forged.structured_data["fields"]["claimant_name"]="forged"
        forged.structured_data["export_manifest"]["md"]["sha256"]="0"*64
        result=self.verify(forged)
        locations={f.output_location for f in result.data.findings}
        self.assertIn("structured_data.field_rows",locations)
        self.assertIn("structured_data.export_manifest",locations)

    def test_saved_report_yields_non_executing_correction_tasks(self):
        target=self.output(missing_fields=["court"])
        review=self.verify(target)
        result=self.service.prepare_output_correction(self.request(snapshot=False,verification_output_id=review.output_refs[0]))
        self.assertEqual(result.status,"insufficient",result.error)
        self.assertEqual(result.data.target_output_id,target.output_id)
        self.assertFalse(result.data.automatically_executed)
        self.assertEqual(result.data.recommended_max_rounds,3)
        self.assertEqual(self.service.assets.get_output(self.case.case_id,target.output_id)["output"],target)

    def test_unsaved_or_forged_candidate_cannot_be_used_as_correction_basis(self):
        target=self.output()
        forged=target.model_copy(update={"text":"changed"})
        review=self.verify(forged)
        result=self.service.prepare_output_correction(self.request(snapshot=False,verification_output_id=review.output_refs[0]))
        self.assertEqual(result.error.code,"invalid_request")

    def test_stale_review_requires_reverification(self):
        review=self.verify(self.output())
        self.advance()
        result=self.service.prepare_output_correction(self.request(snapshot=False,verification_output_id=review.output_refs[0]))
        self.assertEqual(result.status,"error")

    def test_comparison_does_not_claim_semantic_resolution(self):
        first=self.verify(self.output("old",missing_fields=["court"]),"r1")
        second=self.verify(self.output("new"),"r2")
        comparison=self.service.compare_verification_reports(self.request(snapshot=False,
            verification_output_id=second.output_refs[0],previous_verification_output_id=first.output_refs[0]))
        self.assertEqual(comparison.status,"insufficient",comparison.error)
        self.assertTrue(comparison.data.previous_review["no_longer_reported"])
        self.assertFalse(comparison.data.previous_review["semantic_resolution_proven"])

    def test_duplicate_semantic_ids_or_bad_usage_rejected(self):
        finding=dict(finding_id="x",category="conflict",severity="warning",message="test")
        for index,(findings,usage) in enumerate([([finding,finding],None),([],float("nan")),([finding],{"amount":float("nan")})]):
            self.service.verifier.semantic_gateway=SimpleNamespace(review=lambda **_:(findings,usage))
            result=self.verify(self.output(f"target{index}"),f"request{index}")
            self.assertIn("semantic_review_failed",result.data.checks_not_performed)
