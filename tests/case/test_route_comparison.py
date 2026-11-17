"""Regression and handoff tests for case-grounded three-route comparisons."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backend.case import CaseService, SQLiteCaseRepository, load_development_case
from backend.case.planning.contracts import CompareRoutesRequest, CompareRoutesData
from backend.case.calculation.fee_rules import PROPERTY_FEE_RULE_VERSION
from backend.case.models import CaseSnapshot, Money, SourcePointer
from backend.case.planning.route_models import COMPARISON_VERSION
from backend.case.planning.routes import compare_routes


class RouteComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = SQLiteCaseRepository(Path(self.temp.name) / "cases.db")
        self.case = self.repo.create_case(load_development_case())
        self.service = CaseService(self.repo)

    def request(self, **changes):
        request = dict(context=dict(request_id="route-test", case_id=self.case.case_id,
                                    experiment_run_id=None, deadline_at=None),
                       case_version=self.case.case_version, case_snapshot=self.case,
                       available_authorities=[], calculations=[], user_goals=[])
        return {**request, **changes}

    def fee(self, amount="3000.00"):
        result = self.service.calculate_costs(dict(context=self.request()["context"], case_version=self.case.case_version,
            calculation_type="property_case_acceptance_fee", rule_version=PROPERTY_FEE_RULE_VERSION,
            inputs=dict(claim_value=dict(amount=amount, currency="CNY"), case_category="ordinary_property",
                        procedure="first_instance_ordinary", adjustment="none", filing_date="2026-11-09")))
        self.assertEqual(result.status, "ok", result.error)
        return result.data

    def option(self, result, route="litigation"):
        self.assertNotEqual(result.status, "error", result.error)
        return next(item for item in result.data.options if item.route == route)

    def binding_request(self, **changes):
        ref = SourcePointer(source_id="synthetic_rule", source_version="v1")
        binding = dict(binding_id="binding-1", route="mediation", dimension="agreement_performance",
            statement="合成测试候选陈述，未核验语义", source_ref=ref, quote="合成测试原文", conditions=["需核对适用条件"])
        binding.update(changes)
        return self.request(available_authorities=[binding["source_ref"]], source_bindings=[binding])

    @staticmethod
    def source(pointer):
        return dict(source_id=pointer.source_id, version=pointer.source_version, source_type="statute",
            title="合成测试规则", locator={"article": "test-only"}, text="前文。合成测试原文。后文。",
            url="https://example.com/synthetic", effective_from="2020-01-01", verified_on="2026-11-09",
            jurisdiction="CN-Mainland")

    def test_three_options_five_dimensions_without_case_mutation(self):
        before = self.repo.get_case(self.case.case_id)
        result = self.service.compare_routes(self.request())
        self.assertEqual(result.status, "insufficient")
        self.assertEqual(result.data.implementation_version, COMPARISON_VERSION)
        self.assertEqual([item.route for item in result.data.options], ["negotiation", "mediation", "litigation"])
        for item in result.data.options:
            self.assertEqual([row.dimension for row in item.dimensions],
                             ["materials", "costs", "time_steps", "agreement_performance", "risks"])
        self.assertEqual(before, self.repo.get_case(self.case.case_id))
        self.assertTrue(result.output_refs)

    def test_case_findings_and_pending_facts_are_linked(self):
        result = self.service.compare_routes(self.request())
        context = result.data.case_context
        self.assertIn("f_claimant", context.confirmed_fact_ids)
        self.assertIn("f_date_user", context.pending_fact_ids)
        self.assertTrue(any(item.category == "conflict" for item in context.findings))
        self.assertTrue(any(item.category == "duplicate" for item in context.findings))
        for option in result.data.options:
            self.assertEqual(option.dimensions[0].finding_ids, [item.finding_id for item in context.findings])
            self.assertTrue(option.dimensions[0].questions)

    def test_fee_exact_and_estimate_counted_once_and_duplicate_inputs_collapsed(self):
        fee = self.fee()
        duplicate = fee.model_copy(deep=True)
        duplicate.inputs["claim_value"]["amount"] = "3000.0"
        result = self.service.compare_routes(self.request(calculations=[fee, duplicate]))
        option = self.option(result)
        self.assertEqual(len(option.costs), 1)
        self.assertEqual(option.costs[0].decimal, 50)
        self.assertEqual(option.cost_items[0].calculation_indexes, [0, 1])
        self.assertEqual(option.source_refs, [self.service.calculator.fee_rule.pointer])
        self.assertTrue(all(not item.costs for item in result.data.options[:2]))

    def test_material_loss_total_does_not_become_route_expense(self):
        calc = self.service.calculate_costs(dict(context=self.request()["context"], case_version=1,
            calculation_type="sum_amounts", rule_version="arithmetic_v1",
            inputs={"amounts": [{"amount": "3000", "currency": "CNY"}]})).data
        result = self.service.compare_routes(self.request(calculations=[calc, self.fee()]))
        self.assertEqual([item.decimal for item in self.option(result).costs], [50])
        self.assertIn("未映射", result.data.cost_review_issues[0])

    def test_tampered_fee_amount_currency_formula_or_source_is_rejected(self):
        for field in ("amount", "currency", "formula", "source", "version", "extra_amount", "missing_amount"):
            with self.subTest(field=field):
                fee = self.fee()
                if field == "amount": fee.amounts["fee_estimate"] = Money(amount="1", currency="CNY")
                if field == "currency": fee.amounts["fee_exact"] = Money(amount="50", currency="USD")
                if field == "formula": fee.formula = "fee = 1"
                if field == "source": fee.rule_refs = []
                if field == "version": fee.rule_version = "latest"
                if field == "extra_amount": fee.amounts["total"] = Money(amount="50", currency="CNY")
                if field == "missing_amount": fee.amounts.pop("fee_exact")
                result = compare_routes(self.request(calculations=[fee]))
                self.assertFalse(self.option(result).costs)
                self.assertTrue(result.data.cost_review_issues)

    def test_two_fee_scenarios_are_not_added_or_arbitrarily_selected(self):
        result = self.service.compare_routes(self.request(calculations=[self.fee(), self.fee("20000")]))
        self.assertFalse(self.option(result).costs)
        self.assertTrue(any("多组" in issue for issue in result.data.cost_review_issues))

    def test_valid_and_invalid_fee_candidates_do_not_hide_failure(self):
        bad = self.fee().model_copy(update={"unresolved_inputs": ["unknown condition"]})
        result = self.service.compare_routes(self.request(calculations=[self.fee(), bad]))
        self.assertFalse(self.option(result).costs)
        self.assertTrue(any("同时" in issue for issue in result.data.cost_review_issues))

    def test_fee_scope_date_and_special_conditions_rechecked(self):
        for field, value in (("filing_date", "2027-01-01"), ("adjustment", "half"), ("procedure", "appeal")):
            with self.subTest(field=field):
                fee = self.fee(); fee.inputs[field] = value
                self.assertFalse(self.option(compare_routes(self.request(calculations=[fee]))).costs)
        case = self.case.model_copy(update={"jurisdiction": "HK"})
        self.assertFalse(self.option(compare_routes(self.request(case_snapshot=case, calculations=[self.fee()]))).costs)

    def test_unknown_costs_do_not_become_zero_or_duration_promises(self):
        result = self.service.compare_routes(self.request())
        for option in result.data.options:
            self.assertEqual(option.costs, [])
            self.assertTrue(any("不能解释为零" in text for text in option.uncertainties))
            self.assertTrue(any("未生成办结天数" in text for text in option.uncertainties))

    def expense_case(self, *, conflict=False):
        raw = self.case.model_dump(mode="json")
        fact = deepcopy(raw["facts"][2])
        fact.update(fact_id="travel", field="declared_travel_expense", value_type="money",
                    value={"amount": "80", "currency": "CNY"})
        raw["statements"][0]["text"] += "合成测试：确认交通支出80元。"
        raw["facts"].append(fact)
        if conflict:
            alternative = deepcopy(fact)
            alternative.update(fact_id="travel-conflict", confirmation=None, confirmation_status="unconfirmed",
                               value={"amount": "90", "currency": "CNY"})
            raw["facts"].append(alternative)
        return CaseSnapshot.model_validate(raw)

    def test_declared_expenses_need_confirmed_fact_and_deduplicate_within_route(self):
        case = self.expense_case()
        expense = dict(route="negotiation", label="用户自述交通支出", fact_id="travel")
        result = compare_routes(self.request(case_snapshot=case, user_expenses=[expense, expense,
            {**expense, "route": "mediation"}]))
        self.assertEqual(len(self.option(result, "negotiation").cost_items), 1)
        self.assertEqual(self.option(result, "mediation").costs[0].decimal, 80)
        self.assertFalse(self.option(result).costs)
        self.assertTrue(any("重复引用" in issue for issue in result.data.cost_review_issues))

    def test_conflicting_and_unconfirmed_expense_facts_not_used(self):
        for case, fact_id in ((self.expense_case(conflict=True), "travel"), (self.case, "f_repair_amount"), (self.case, "absent")):
            result = compare_routes(self.request(case_snapshot=case,
                user_expenses=[dict(route="litigation", label="测试", fact_id=fact_id)]))
            self.assertFalse(self.option(result).costs)

    def test_superseded_expense_fact_not_used(self):
        raw = self.expense_case().model_dump(mode="json")
        old = raw["facts"][-1]
        replacement = deepcopy(old); replacement["fact_id"] = "travel-new"
        old["superseded_by"] = "travel-new"
        raw["facts"].append(replacement)
        result = compare_routes(self.request(case_snapshot=raw,
            user_expenses=[dict(route="litigation", label="旧金额", fact_id="travel")]))
        self.assertFalse(self.option(result).costs)

    def test_source_quote_locator_and_only_matching_route_are_attached(self):
        self.service.external_source_resolver = self.source
        result = self.service.compare_routes(self.binding_request())
        check = result.data.source_checks[0]
        self.assertEqual(check.status, "original_text_checked")
        self.assertFalse(check.semantic_support_checked)
        self.assertEqual(check.source.quote_start, 3)
        self.assertEqual(check.source.quote_end, 9)
        self.assertEqual(check.source.text_sha256, hashlib.sha256("前文。合成测试原文。后文。".encode()).hexdigest())
        self.assertTrue(self.option(result, "mediation").source_refs)
        self.assertFalse(self.option(result, "negotiation").source_refs)
        self.assertEqual(self.option(result, "mediation").dimensions[3].source_binding_ids, ["binding-1"])
        stored = self.service.assets.get_output(self.case.case_id, result.output_refs[0])["output"]
        self.assertEqual(stored.source_refs, result.source_refs)

    def test_missing_resolver_or_unmatched_quote_keeps_other_routes(self):
        for reviewer in (None, self.source):
            self.service.external_source_resolver = reviewer
            request = self.binding_request(quote="does not occur")
            request["context"]["request_id"] = str(reviewer is None)
            result = self.service.compare_routes(request)
            self.assertEqual(result.data.source_checks[0].status, "rejected")
            self.assertEqual(len(result.data.options), 3)
            self.assertFalse(result.source_refs)

    def test_source_wrong_version_case_type_or_no_locator_rejected(self):
        for field, value in (("version", "v2"), ("source_type", "case"), ("locator", {})):
            self.service.external_source_resolver = lambda pointer: {**self.source(pointer), field: value}
            request = self.binding_request(); request["context"]["request_id"] = field
            result = self.service.compare_routes(request)
            self.assertEqual(result.data.source_checks[0].status, "rejected")
            self.assertFalse(result.source_refs)

    def test_partial_source_failure_retains_valid_binding(self):
        self.service.external_source_resolver = self.source
        request = self.binding_request()
        bad = {**request["source_bindings"][0], "binding_id": "bad", "quote": "missing quote"}
        request["source_bindings"].append(bad)
        result = self.service.compare_routes(request)
        self.assertEqual([item.status for item in result.data.source_checks], ["original_text_checked", "rejected"])
        self.assertTrue(result.source_refs)

    def test_unbound_authority_is_not_accepted_as_route_support(self):
        self.service.external_source_resolver = self.source
        result = self.service.compare_routes(self.request(available_authorities=[SourcePointer(source_id="x", source_version="1")]))
        self.assertFalse(result.source_refs)
        self.assertTrue(any("尚未绑定" in text for text in result.data.unresolved_items))

    def test_binding_requires_pinned_available_source_and_unique_id(self):
        for mode in ("unlisted", "unpinned", "duplicate"):
            request = self.binding_request()
            if mode == "unlisted": request["available_authorities"] = []
            if mode == "unpinned": request["source_bindings"][0]["source_ref"].source_version = None
            if mode == "duplicate": request["source_bindings"] *= 2
            self.assertEqual(self.service.compare_routes(request).error.code, "invalid_request")

    def test_source_warnings_short_excerpt_and_multiple_versions_retained(self):
        self.service.external_source_resolver = lambda pointer: {**self.source(pointer), "text_scope": "short_excerpt_only"}
        request = self.binding_request()
        ref = SourcePointer(source_id="synthetic_rule", source_version="v2")
        request["available_authorities"].append(ref)
        request["source_bindings"].append({**request["source_bindings"][0], "binding_id": "v2", "source_ref": ref})
        result = self.service.compare_routes(request)
        self.assertEqual(len(result.source_refs), 2)
        self.assertTrue(all(any("多个选定版本" in text for text in item.source.warnings) for item in result.data.source_checks))
        self.assertTrue(any("短引文" in text for text in result.data.source_checks[0].source.warnings))

    def test_goals_map_dimensions_without_ranking_or_switching_route(self):
        result = self.service.compare_routes(self.request(user_goals=["节省成本", "尽快解决", "不要自动选诉讼"]))
        focus = {item.goal: item for item in result.data.goal_focus}
        self.assertEqual(focus["节省成本"].dimensions, ["costs"])
        self.assertEqual(focus["尽快解决"].dimensions, ["time_steps"])
        self.assertEqual(focus["不要自动选诉讼"].dimensions, [])
        self.assertIsNone(self.repo.get_case(self.case.case_id).selected_route)

    def test_deadline_and_forged_snapshot_do_not_save_outputs(self):
        request = self.request()
        request["context"]["deadline_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        result = self.service.compare_routes(request)
        self.assertEqual(result.error.code, "timeout")
        self.assertFalse(result.output_refs)
        forged = self.case.model_copy(update={"user_goals": ["forged"]})
        self.assertEqual(self.service.compare_routes(self.request(case_snapshot=forged)).error.code, "invalid_request")

    def test_version_conflict_is_preserved(self):
        future = self.case.model_copy(update={"case_version": 2})
        result = self.service.compare_routes(self.request(case_version=2, case_snapshot=future))
        self.assertEqual(result.error.code, "version_conflict")

    def test_source_timeout_does_not_save_partial_output(self):
        def timeout(pointer):
            raise TimeoutError("source timeout")
        self.service.external_source_resolver = timeout
        result = self.service.compare_routes(self.binding_request())
        self.assertEqual(result.error.code, "timeout")
        self.assertFalse(result.output_refs)

    def test_idempotency_and_request_id_reuse(self):
        request = self.request()
        first = self.service.compare_routes(request)
        self.assertEqual(first, self.service.compare_routes(request))
        self.assertEqual(self.service.compare_routes(self.request(user_goals=["节省成本"])).error.code, "invalid_request")

    def test_old_request_digest_and_legacy_result_remain_compatible(self):
        request = CompareRoutesRequest.model_validate(self.request())
        raw = request.model_dump(mode="json")
        raw.pop("source_bindings"); raw.pop("user_expenses")
        old_digest = hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(self.service.outputs.request_digest(request), old_digest)
        legacy = CompareRoutesData.model_validate({"options": [], "assumptions": [], "unresolved_items": []})
        self.assertEqual(legacy.implementation_version, "civil_comparison_v1")
        self.assertIsNone(legacy.case_context)

    def test_schema_and_input_bounds(self):
        from jsonschema import Draft202012Validator
        result = self.service.compare_routes(self.request())
        Draft202012Validator(CompareRoutesData.model_json_schema()).validate(result.data.model_dump(mode="json"))
        self.assertEqual(self.service.compare_routes(self.request(user_goals=["x"] * 31)).error.code, "invalid_request")
        self.assertEqual(self.service.compare_routes(self.request(user_expenses=[
            dict(route="negotiation", label=" ", fact_id="travel")])).error.code, "invalid_request")


if __name__ == "__main__":
    unittest.main()
