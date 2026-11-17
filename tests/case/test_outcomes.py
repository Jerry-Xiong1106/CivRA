"""Claim-level analysis preparation, source checks and candidate containment."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backend.case import CaseService, SQLiteCaseRepository, load_development_case
from backend.case.models import CaseSnapshot
from backend.case.research.outcomes import OutcomeAnalysisData, OutcomeAnalysisRequest


class OutcomeAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.repo = SQLiteCaseRepository(Path(self.temp.name) / "cases.db")
        self.case = self.repo.create_case(load_development_case())
        self.service = CaseService(self.repo, source_resolver=self.source)
        self.counter = 0

    @staticmethod
    def source(pointer):
        return dict(source_id=pointer.source_id, version=pointer.source_version,
            source_type="case" if pointer.source_id == "test-case" else "statute", title="合成测试原文",
            locator={"paragraph": "synthetic-only"}, text="合成测试内容。条件需要另行核实。", url="https://example.com/synthetic",
            effective_from="2020-01-01", court="合成法院" if pointer.source_id == "test-case" else None,
            decision_date="2025-01-01" if pointer.source_id == "test-case" else None, jurisdiction="CN-Mainland")

    def request(self, **changes):
        self.counter += 1
        case = self.repo.get_case(self.case.case_id)
        return {"context": dict(request_id=f"outcomes-{self.counter}", case_id=case.case_id,
                                 experiment_run_id=None, deadline_at=None),
                "case_version": case.case_version, "case_snapshot": case, **changes}

    def inputs(self):
        return dict(source_selections=[
            dict(selection_id="law", source_ref=dict(source_id="test-law", source_version="v1"), expected_kind="statute", quote="合成测试内容。"),
            dict(selection_id="case", source_ref=dict(source_id="test-case", source_version="v1"), expected_kind="case", quote="合成测试内容。")],
            factors=[dict(factor_id="support-factor", claim_id="claim_repair", polarity="supporting", description="合成的有利候选描述",
                          fact_ids=["f_claimant"], evidence_ids=["e_invoice"], source_selection_ids=["law"]),
                     dict(factor_id="adverse-factor", claim_id="claim_repair", polarity="adverse", description="日期有不同记载，需核实",
                          fact_ids=["f_date_user", "f_date_document"], source_selection_ids=[])],
            case_comparisons=[dict(comparison_id="case-difference", claim_id="claim_repair", source_selection_id="case",
                                   fact_ids=["f_claimant"], similarities=["合成相似点"], differences=["合成差异，需语义核验"])],
            scenarios=[dict(scenario_id="candidate-support", claim_id="claim_repair", kind="support", statement="如果列明条件得到核实，再讨论请求获支持的情形。",
                            conditions=["本案事实和规则适用得到核实"], factor_ids=["support-factor"], source_selection_ids=["law"])])

    def analyze(self, **changes):
        result = self.service.analyze_outcomes(self.request(**changes))
        self.assertEqual(result.status, "insufficient", result.error)
        return result

    def replace(self, mutate):
        snapshot = self.repo.get_case(self.case.case_id)
        raw = snapshot.model_dump(mode="json"); mutate(raw)
        raw["case_version"] += 1
        raw["updated_at"] = max(datetime.now(timezone.utc), snapshot.updated_at).isoformat()
        return self.repo.replace_case(CaseSnapshot.model_validate(raw), expected_case_version=snapshot.case_version)

    def test_default_is_conditional_framework_not_applied_prediction(self):
        before = self.repo.get_case(self.case.case_id)
        result = self.analyze()
        claim = result.data.claims[0]
        self.assertEqual({item.kind for item in claim.scenarios}, {"support", "partial_support", "undetermined"})
        self.assertTrue(all(item.status == "template_not_applied" and not item.semantic_support_checked for item in claim.scenarios))
        self.assertTrue(any(item.category == "conflict" for item in result.data.findings))
        self.assertTrue(claim.pending_facts)
        self.assertEqual(self.repo.get_case(self.case.case_id), before)
        self.assertTrue(any("不代表不存在" in text for text in claim.unresolved_items))
        self.assertNotIn("source_identity_version_quote", result.data.checks_performed)
        self.assertNotIn("case_comparison_source_kind", result.data.checks_performed)

    def test_factors_preserve_both_sides_and_pending_premises(self):
        result = self.analyze(**self.inputs())
        factors = result.data.claims[0].factors
        self.assertEqual([item.candidate.polarity for item in factors], ["supporting", "adverse"])
        self.assertEqual(factors[0].status, "references_checked")
        self.assertEqual(factors[1].status, "premises_pending")
        self.assertEqual(set(factors[1].pending_fact_ids), {"f_date_user", "f_date_document"})
        self.assertTrue(all(not item.semantic_support_checked for item in factors))

    def test_source_cards_and_case_differences_keep_metadata(self):
        result = self.analyze(**self.inputs())
        self.assertTrue(all(item.status == "original_text_checked" for item in result.data.sources))
        card = next(item.source for item in result.data.sources if item.selection_id == "case")
        self.assertEqual(card.metadata["court"], "合成法院")
        self.assertEqual(card.quote_start, 0)
        comparison = result.data.claims[0].case_comparisons[0]
        self.assertTrue(comparison.candidate.differences)
        self.assertFalse(comparison.semantic_comparison_checked)

    def test_valid_candidate_is_still_needs_review_and_request_amount_not_award(self):
        result = self.analyze(**self.inputs())
        scenario = result.data.claims[0].scenarios[-1]
        self.assertEqual(scenario.origin, "caller")
        self.assertEqual(scenario.status, "candidate_needs_review")
        self.assertFalse(scenario.semantic_support_checked)
        self.assertEqual(result.data.claims[0].claim.amount.amount, "3000.00")
        self.assertNotIn("awarded_amount", result.data.model_dump_json())

    def test_unconfirmed_factor_does_not_become_confirmed_scenario_premise(self):
        inputs = self.inputs(); inputs["scenarios"][0]["factor_ids"] = ["adverse-factor"]
        result = self.analyze(**inputs)
        self.assertEqual(result.data.claims[0].scenarios[-1].status, "premises_pending")

    def test_unknown_fact_or_evidence_rejects_factor_not_whole_report(self):
        for field in ("fact_ids", "evidence_ids", "source_selection_ids"):
            inputs = self.inputs(); inputs["factors"][0][field] = ["absent"]
            result = self.analyze(**inputs)
            self.assertEqual(len(result.data.claims), 1)
            self.assertTrue(any(item["kind"] == "factor" for item in result.data.rejected_candidates))
            self.assertTrue(any(item["kind"] == "scenario" for item in result.data.rejected_candidates))
            self.assertEqual(len(result.data.claims[0].scenarios), 3)

    def test_rejected_source_removes_dependent_candidate_only(self):
        inputs = self.inputs(); inputs["source_selections"][0]["quote"] = "不存在的引用"
        result = self.analyze(**inputs)
        self.assertEqual([item.status for item in result.data.sources], ["rejected", "original_text_checked"])
        self.assertEqual(len(result.data.claims[0].case_comparisons), 1)
        self.assertEqual([ref.source_id for ref in result.source_refs], ["test-case"])

    def test_missing_resolver_preserves_framework_and_reports_rejections(self):
        self.service.external_source_resolver = None
        result = self.analyze(**self.inputs())
        self.assertEqual(len(result.data.claims[0].scenarios), 3)
        self.assertTrue(all(item.status == "rejected" for item in result.data.sources))
        self.assertFalse(result.source_refs)

    def test_statute_cannot_be_used_as_similar_case(self):
        inputs = self.inputs(); inputs["case_comparisons"][0]["source_selection_id"] = "law"
        result = self.analyze(**inputs)
        self.assertFalse(result.data.claims[0].case_comparisons)
        self.assertTrue(any(item["kind"] == "case_comparison" for item in result.data.rejected_candidates))

    def test_empty_case_difference_is_visible_not_silently_complete(self):
        inputs = self.inputs(); inputs["case_comparisons"][0]["differences"] = []
        result = self.analyze(**inputs)
        self.assertTrue(any("差异尚未" in text for text in result.data.claims[0].case_comparisons[0].limitations))

    def test_case_only_support_scenario_requires_legal_source(self):
        inputs = self.inputs(); inputs["scenarios"][0]["source_selection_ids"] = ["case"]
        result = self.analyze(**inputs)
        self.assertEqual(len(result.data.claims[0].scenarios), 3)
        self.assertTrue(any(item["kind"] == "scenario" for item in result.data.rejected_candidates))

    def test_candidate_without_conditions_or_with_probability_is_invalid(self):
        for field, value in (("conditions", []), ("statement", "胜诉概率80%"), ("win_probability", 0.8),
                             ("scenario_id", "template:claim_repair:support")):
            inputs = self.inputs(); inputs["scenarios"][0][field] = value
            result = self.service.analyze_outcomes(self.request(**inputs))
            self.assertEqual(result.error.code, "invalid_request")

    def test_unknown_withdrawn_and_duplicate_claim_selection_rejected(self):
        for claims in (["absent"], ["claim_repair", "claim_repair"], []):
            self.assertEqual(self.service.analyze_outcomes(self.request(claim_ids=claims)).error.code, "invalid_request")
        self.replace(lambda raw: raw["claims"][0].update(status="withdrawn"))
        self.assertEqual(self.service.analyze_outcomes(self.request(claim_ids=["claim_repair"])).error.code, "invalid_request")
        self.assertEqual(self.service.analyze_outcomes(self.request()).status, "empty")

    def test_candidate_cannot_use_factor_from_another_claim(self):
        def add_claim(raw):
            claim = deepcopy(raw["claims"][0]); claim["claim_id"] = "claim-other"; raw["claims"].append(claim)
        self.replace(add_claim)
        inputs = self.inputs(); inputs["scenarios"][0]["claim_id"] = "claim-other"
        result = self.analyze(**inputs)
        self.assertTrue(any(item["kind"] == "scenario" for item in result.data.rejected_candidates))
        self.assertEqual(len(result.data.claims), 2)

    def test_candidate_for_unselected_claim_rejected(self):
        inputs = self.inputs(); inputs["factors"][0]["claim_id"] = "other-case-claim"
        self.assertEqual(self.service.analyze_outcomes(self.request(**inputs)).error.code, "invalid_request")

    def test_duplicate_ids_and_unpinned_versions_rejected(self):
        for field in ("factors", "source_selections", "case_comparisons", "scenarios"):
            inputs = self.inputs(); inputs[field].append(deepcopy(inputs[field][0]))
            self.assertEqual(self.service.analyze_outcomes(self.request(**inputs)).error.code, "invalid_request")
        inputs = self.inputs(); inputs["source_selections"][0]["source_ref"]["source_version"] = None
        self.assertEqual(self.service.analyze_outcomes(self.request(**inputs)).error.code, "invalid_request")
        inputs = self.inputs(); inputs["source_selections"][0]["quote"] = " "
        self.assertEqual(self.service.analyze_outcomes(self.request(**inputs)).error.code, "invalid_request")

    def test_saved_links_can_be_used_by_existing_verifier(self):
        result = self.analyze(**self.inputs())
        output = self.service.assets.get_output(self.case.case_id, result.output_refs[0])["output"]
        self.assertTrue(output.links)
        self.assertEqual(output.links[0].fact_ids, ["f_claimant"])
        verify = self.service.verify_output(self.request(candidate_output=output, cited_sources=output.source_refs))
        self.assertEqual(verify.status, "insufficient")
        self.assertIn("semantic_citation_support", verify.data.checks_not_performed)
        self.assertIn("adverse_material_review", verify.data.checks_not_performed)

    def test_reference_source_not_bound_to_claim_is_not_conclusion_citation(self):
        inputs = self.inputs()
        result = self.analyze(source_selections=inputs["source_selections"])
        self.assertTrue(result.data.sources)
        self.assertFalse(result.source_refs)

    def test_idempotency_and_changed_arguments(self):
        request = self.request(**self.inputs())
        first = self.service.analyze_outcomes(request)
        self.assertEqual(first, self.service.analyze_outcomes(request))
        request["factors"][0]["description"] = "changed candidate"
        self.assertEqual(self.service.analyze_outcomes(request).error.code, "invalid_request")

    def test_stale_or_forged_snapshot_and_timeout_rejected(self):
        request = self.request()
        request["case_snapshot"] = request["case_snapshot"].model_copy(update={"user_goals": ["forged"]})
        self.assertEqual(self.service.analyze_outcomes(request).error.code, "invalid_request")
        request = self.request()
        self.replace(lambda raw: raw.update(user_goals=["new"]))
        self.assertEqual(self.service.analyze_outcomes(request).error.code, "version_conflict")
        request = self.request(); request["context"]["deadline_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        result = self.service.analyze_outcomes(request)
        self.assertEqual(result.error.code, "timeout"); self.assertFalse(result.output_refs)

    def test_source_timeout_does_not_save_partial_analysis(self):
        def timeout(pointer): raise TimeoutError("source timed out")
        self.service.external_source_resolver = timeout
        result = self.service.analyze_outcomes(self.request(**self.inputs()))
        self.assertEqual(result.error.code, "timeout")
        self.assertFalse(result.output_refs)

    def test_no_new_agent_tool_registration_and_schemas_validate(self):
        from jsonschema import Draft202012Validator
        # The new Python entry is explicitly kept out of the shared ten-tool table.
        from backend.case.operations import REQUEST_MODELS
        self.assertEqual(len(REQUEST_MODELS), 10)
        self.assertNotIn("analyze_outcomes", REQUEST_MODELS)
        result = self.analyze(**self.inputs())
        Draft202012Validator(OutcomeAnalysisData.model_json_schema()).validate(result.data.model_dump(mode="json"))
        Draft202012Validator.check_schema(OutcomeAnalysisRequest.model_json_schema())


if __name__ == "__main__":
    unittest.main()
