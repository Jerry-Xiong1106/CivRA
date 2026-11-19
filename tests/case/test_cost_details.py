"""Independent amount expectations, fee bands, exclusions and reviewable material totals."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backend.case import CaseService, SQLiteCaseRepository
from scripts.case.fixtures import load_development_case
from backend.tools.case_results import to_runtime_tool_result
from backend.case.calculation.cost_summary import MATERIAL_SUMMARY_VERSION, summarize_material_amounts
from backend.case.calculation.fee_rules import PROPERTY_FEE_RULE_VERSION
from backend.case.models import BusinessOutput, CaseSnapshot, Fact


class CostDetailTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = SQLiteCaseRepository(Path(self.temp.name)/"cases.db")
        self.case = self.repo.create_case(load_development_case())
        self.service = CaseService(self.repo)

    def context(self):
        return {"request_id": "cost-review", "case_id": self.case.case_id, "experiment_run_id": None, "deadline_at": None}

    def fee_request(self, amount="100000", **changes):
        inputs = {"claim_value": {"amount": amount, "currency": "CNY"}, "case_category": "ordinary_property",
                  "procedure": "first_instance_ordinary", "adjustment": "none", "filing_date": "2026-11-09"}
        inputs.update(changes)
        return {"context": self.context(), "case_version": 1, "calculation_type": "property_case_acceptance_fee",
                "rule_version": PROPERTY_FEE_RULE_VERSION, "inputs": inputs}

    def fee(self, amount="100000", **changes):
        return self.service.calculate_costs(self.fee_request(amount, **changes))

    def confirmed_payload(self):
        payload = self.case.model_dump(mode="json")
        for fact in payload["facts"]:
            if fact["value_type"] == "money":
                fact["confirmation_status"] = "confirmed"
                fact["confirmation"] = deepcopy(payload["facts"][2]["confirmation"])
        return payload

    def summary(self, payload, ids):
        return summarize_material_amounts(CaseSnapshot.model_validate(payload), ids)

    def test_all_fee_band_boundaries_have_independent_expected_totals(self):
        for amount, expected in [("1", "50"), ("10000", "50"), ("100000", "2300"), ("200000", "4300"),
                                 ("500000", "8800"), ("1000000", "13800"), ("2000000", "22800"),
                                 ("5000000", "46800"), ("10000000", "81800"), ("20000000", "141800"), ("30000000", "191800")]:
            with self.subTest(amount=amount):
                result = self.fee(amount)
                self.assertEqual(result.status, "ok", result.error)
                self.assertEqual(result.data.amounts["fee_exact"].decimal, Decimal(expected))
                self.assertEqual(result.data.amounts["fee_estimate"].decimal, Decimal(expected))
                self.assertNotIn("total", result.data.amounts)

    def test_fee_cent_below_at_and_above_boundaries_matches_affine_oracle(self):
        # Independent shortcut table from SPC fee guidance; production sums marginal bands.
        formulas = [(Decimal("10000"), Decimal("0"), Decimal("50")),
                    (Decimal("100000"), Decimal(".025"), Decimal("-200")),
                    (Decimal("200000"), Decimal(".02"), Decimal("300")),
                    (Decimal("500000"), Decimal(".015"), Decimal("1300")),
                    (Decimal("1000000"), Decimal(".01"), Decimal("3800")),
                    (Decimal("2000000"), Decimal(".009"), Decimal("4800")),
                    (Decimal("5000000"), Decimal(".008"), Decimal("6800")),
                    (Decimal("10000000"), Decimal(".007"), Decimal("11800")),
                    (Decimal("20000000"), Decimal(".006"), Decimal("21800")),
                    (Decimal("Infinity"), Decimal(".005"), Decimal("41800"))]
        for boundary, _, _ in formulas[:-1]:
            for delta in [Decimal("-.01"), Decimal("0"), Decimal(".01")]:
                amount = boundary+delta
                _, rate, offset = next(row for row in formulas if amount <= row[0])
                with self.subTest(amount=amount):
                    self.assertEqual(self.fee(format(amount, "f")).data.amounts["fee_exact"].decimal, amount*rate+offset)

    def test_rounding_is_explicit_and_exact_value_is_retained(self):
        result = self.fee("10000.20")
        self.assertEqual(result.data.amounts["fee_exact"].decimal, Decimal("50.005"))
        self.assertEqual(result.data.amounts["fee_estimate"].amount, "50.01")
        self.assertTrue(any("工程展示约定" in text for text in result.data.assumptions))

    def test_unknown_fee_scope_or_special_adjustment_is_rejected(self):
        for change in [{"case_category": "non_property"}, {"procedure": "summary"}, {"adjustment": "half"},
                       {"adjustment": "waiver"}, {"procedure": "appeal"}, {"extra_discount": "0.5"}]:
            with self.subTest(change=change):
                self.assertEqual(self.fee(**change).error.code, "invalid_request")

    def test_fee_does_not_accept_unknown_zero_float_currency_or_extra_precision(self):
        for value in ["0", "-1", "1.001", 100.1, None]:
            with self.subTest(value=value):
                self.assertEqual(self.fee(value).error.code, "invalid_request")
        self.assertEqual(self.fee(claim_value={"amount": "100", "currency": "USD"}).error.code, "invalid_request")

    def test_filing_date_is_required_and_must_be_within_checked_window(self):
        for value in ["2007-03-31", "2026-11-10"]:
            with self.subTest(value=value):
                result = self.fee(filing_date=value)
                self.assertEqual(result.status, "insufficient")
                self.assertEqual(result.data.amounts, {})
                self.assertTrue(result.data.unresolved_inputs)
        self.assertEqual(self.fee(filing_date="2007-04-01").status, "ok")
        self.assertEqual(self.fee(filing_date=0).error.code, "invalid_request")
        request = self.fee_request()
        del request["inputs"]["filing_date"]
        self.assertEqual(self.service.calculate_costs(request).error.code, "invalid_request")

    def test_pinned_fee_source_is_resolvable_and_cannot_be_replaced(self):
        result = self.fee()
        self.assertEqual(len(result.source_refs), 1)
        source = self.service.get_source(result.source_refs[0])
        self.assertIn("第十三条", source["locator"])
        self.assertEqual(source["text_scope"], "short_excerpt_only")
        self.assertTrue(source["source_id"].startswith("member3.local_rule."))
        self.assertEqual(to_runtime_tool_result(result, source_resolver=self.service.get_source)["data"]["matches"][0]["source_ref"]["source_version"], source["version"])
        wrong = result.source_refs[0].model_copy(update={"source_version": "latest"})
        with self.assertRaises(KeyError):
            self.service.get_source(wrong)
        request = self.fee_request()
        request["rule_refs"] = [wrong]
        self.assertEqual(self.service.calculate_costs(request).error.code, "invalid_request")

    def test_fee_foreign_jurisdiction_stale_version_and_deadline(self):
        payload = self.case.model_dump(mode="json")
        payload.update(case_id="outside", jurisdiction="outside-mainland")
        other = self.repo.create_case(CaseSnapshot.model_validate(payload))
        request = self.fee_request()
        request["context"]["case_id"] = other.case_id
        self.assertEqual(self.service.calculate_costs(request).error.code, "invalid_request")
        request = self.fee_request()
        request["case_version"] = 2
        self.assertEqual(self.service.calculate_costs(request).error.code, "version_conflict")
        request = self.fee_request()
        request["context"]["deadline_at"] = datetime.now(timezone.utc)-timedelta(seconds=1)
        self.assertEqual(self.service.calculate_costs(request).error.code, "timeout")

    def test_mutating_returned_source_pointer_cannot_change_registry_version(self):
        first = self.fee()
        first.source_refs[0].source_version = "forged"
        second = self.fee()
        self.assertEqual(second.status, "ok")
        self.assertNotEqual(second.source_refs[0].source_version, "forged")
        self.assertEqual(self.service.get_source(second.source_refs[0])["version"], second.source_refs[0].source_version)

    def test_fee_record_can_be_replayed_by_output_verifier(self):
        calculated = self.fee()
        output = BusinessOutput(output_id="fee-demo", output_version=1, case_id=self.case.case_id, case_version=1,
            output_type="analysis", text="本地规则费用估算", source_refs=calculated.source_refs,
            structured_data={"calculations": [calculated.data.model_dump(mode="json")]})
        result = self.service.verifier.verify_output({"context": self.context(), "case_version": 1,
            "case_snapshot": self.case, "candidate_output": output, "cited_sources": calculated.source_refs})
        self.assertFalse(any(item.category in {"calculation", "citation"} for item in result.data.findings))
        self.assertIn("structured_calculation_replay", result.data.checks_performed)

    def test_fee_and_material_review_do_not_mutate_case(self):
        self.fee()
        result = self.service.summarize_material_costs({"context": self.context(), "case_version": 1,
            "calculation_type": "materials_breakdown", "rule_version": MATERIAL_SUMMARY_VERSION,
            "inputs": {"fact_ids": ["f_repair_amount"]}})
        self.assertEqual(result.status, "insufficient")
        self.assertIsNone(result.data.total)
        self.assertEqual(self.repo.get_case(self.case.case_id), self.case)

    def test_exact_duplicate_occurrence_is_counted_once(self):
        report = self.summary(self.confirmed_payload(), ["f_repair_amount", "f_repair_amount_copy"])
        self.assertEqual(report.total.amount, "3000.00")
        self.assertEqual(sorted(row.state for row in report.rows), ["duplicate", "included"])
        self.assertEqual(report.known_category_subtotals["repair_amount.CNY"].amount, "3000.00")

    def test_different_positions_in_same_pdf_are_not_collapsed_by_hash(self):
        payload = self.confirmed_payload()
        second = payload["facts"][4]
        second["material_refs"][0].update(page=2)
        report = self.summary(payload, ["f_repair_amount", "f_repair_amount_copy"])
        self.assertIsNone(report.total)
        self.assertEqual(len(report.rows), 2)
        self.assertTrue(all(row.state == "unresolved" and "多个位置" in row.reason for row in report.rows))

    def test_unconfirmed_conflicting_candidate_blocks_selected_confirmed_value(self):
        payload = self.confirmed_payload()
        payload["facts"][4].update(confirmation_status="unconfirmed", confirmation=None)
        payload["facts"][4]["value"]["amount"] = "4000.00"
        report = self.summary(payload, ["f_repair_amount"])
        self.assertIsNone(report.total)
        self.assertIn("冲突", report.rows[0].reason)

    def test_excluded_material_is_visible_but_not_totalled(self):
        payload = self.confirmed_payload()
        payload["evidence"][2].update(status="excluded", duplicate_of="e_invoice", exclusion=payload["facts"][2]["confirmation"])
        report = self.summary(payload, ["f_repair_amount", "f_repair_amount_copy"])
        self.assertEqual(report.total.amount, "3000.00")
        self.assertEqual(report.rows[1].state, "excluded")
        self.assertIsNone(self.summary(payload, ["f_repair_amount_copy"]).total)

    def test_unknown_category_and_missing_money_are_not_zero(self):
        payload = self.confirmed_payload()
        payload["facts"][3]["field"] = "court_fee"
        report = self.summary(payload, ["f_repair_amount"])
        self.assertIsNone(report.total)
        self.assertEqual(report.rows[0].state, "unresolved")
        report = self.summary(self.case.model_dump(mode="json"), ["f_claimant"])
        self.assertIsNone(report.total)
        self.assertIsNone(report.rows[0].amount)

    def test_missing_material_location_cannot_be_silently_deduplicated(self):
        payload = self.confirmed_payload()
        payload["facts"][3]["material_refs"][0].update(page=1, span_start=None, span_end=None)
        report = self.summary(payload, ["f_repair_amount"])
        self.assertIsNone(report.total)
        self.assertIn("原文区间", report.rows[0].reason)

    def test_mixed_currency_subtotals_are_separate_without_total(self):
        payload = self.confirmed_payload()
        payload["facts"][4]["field"] = "medical_amount"
        payload["facts"][4]["value"] = {"amount": "100", "currency": "USD"}
        report = self.summary(payload, ["f_repair_amount", "f_repair_amount_copy"])
        self.assertIsNone(report.total)
        self.assertEqual(set(report.known_category_subtotals), {"repair_amount.CNY", "medical_amount.USD"})
        self.assertTrue(any("多币种" in item for item in report.unresolved_inputs))

    def test_invalid_selection_is_rejected(self):
        for ids in [[], "f_repair_amount", ["missing"], ["f_repair_amount", "f_repair_amount"], [1]]:
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                self.summary(self.confirmed_payload(), ids)

    def test_different_file_text_similarity_blocks_total_until_review(self):
        payload = self.case.model_dump(mode="json")
        payload.update(case_id="similar_costs", evidence=[], claims=[], facts=[payload["facts"][2]])
        case = self.repo.create_case(CaseSnapshot.model_validate(payload))
        text = ("【合成维修票据】维修金额：3000.00元\n票据编号：SYN001\n"
                "维修说明：更换车辆前部损坏部件，检验原始票据、维修时间、金额、费用项目和实际支付情况。"
                "备注：这是开发用的合成示例材料，并非真实事故材料，不表示材料真实性或法院采纳，须先核对原图并确认两次上传是否为同一份文件。")
        context = {**self.context(), "case_id": case.case_id}
        for file_id, content in [("a", text), ("b", text.replace("\n", "  \n"))]:
            self.service.assets.put_file(case_id=case.case_id, file_id=file_id, file_version="1", file_name=file_id,
                media_type="text/plain", document_type="repair_invoice", content=content.encode())
        parsed = self.service.parse_documents({"context": context, "case_version": 1, "file_ids": ["a", "b"]})
        self.assertEqual(len(parsed.data.extracted_facts), 2)
        confirmed = [fact.model_copy(update={"confirmation_status": "confirmed", "confirmation": case.facts[0].confirmation}) for fact in parsed.data.extracted_facts]
        saved = self.service.update_case({"context": context, "expected_case_version": 1,
            "changes": {"upsert_facts": confirmed, "upsert_evidence": parsed.data.documents}, "change_reason": "synthetic explicit amount confirmation"})
        self.assertEqual(saved.status, "ok", saved.error)
        request = {"context": context, "case_version": 2, "calculation_type": "materials_breakdown",
                   "rule_version": MATERIAL_SUMMARY_VERSION, "inputs": {"fact_ids": [fact.fact_id for fact in confirmed]}}
        detailed = self.service.summarize_material_costs(request)
        tool_result = self.service.calculate_costs(request)
        self.assertIsNone(detailed.data.total)
        self.assertTrue(all(row.state == "unresolved" for row in detailed.data.rows))
        self.assertEqual(tool_result.data.amounts, {})
        self.assertEqual(tool_result.status, "insufficient")

    def test_new_material_rule_is_explicit_and_legacy_stays_available(self):
        self.assertEqual(self.service.calculate_costs({"context": self.context(), "case_version": 1,
            "calculation_type": "sum_amounts", "rule_version": "arithmetic_v1",
            "inputs": {"amounts": [{"amount": "0.1", "currency": "CNY"}, {"amount": "0.2", "currency": "CNY"}]}}).data.amounts["total"].amount, "0.3")
        request = self.fee_request()
        request["rule_version"] = "latest"
        self.assertEqual(self.service.calculate_costs(request).error.code, "invalid_request")
        with self.assertRaises(ValueError):
            self.service.calculator.register_rule("property_case_acceptance_fee", PROPERTY_FEE_RULE_VERSION, lambda value: None)


    def test_arithmetic_subtraction_supports_multiple_deductions_without_inventing_values(self):
        def amount(value, currency="CNY"):
            return {"amount": value, "currency": currency}
        def subtract(deductions):
            return self.service.calculate_costs({"context": self.context(), "case_version": 1,
                "calculation_type": "subtract_amounts", "rule_version": "arithmetic_v1",
                "inputs": {"base": amount("73.10"), "deductions": deductions}})
        ok = subtract([amount("20"), amount("0.11")])
        self.assertEqual(ok.data.amounts["total"].amount, "52.99")
        excessive = subtract([amount("80")])
        self.assertEqual(excessive.status, "insufficient")
        self.assertFalse(excessive.data.amounts)
        self.assertEqual(subtract([]).error.code, "invalid_request")
        self.assertEqual(subtract([amount("15", "USD")]).error.code, "invalid_request")


if __name__ == "__main__":
    unittest.main()
