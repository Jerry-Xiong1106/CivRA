"""Feature 3 development cases; not independent legal or OCR accuracy evaluation."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backend.case import CaseService, SQLiteCaseRepository, load_development_case
from backend.case.materials.evidence import check_evidence, build_evidence_directory
from backend.case.materials.evidence_rules import EvidenceRules, load_evidence_rules
from backend.case.materials.evidence_similarity import inspect_text_similarity, read_material_text, SimilarityPolicy
from backend.case.models import CaseSnapshot, Evidence


LONG_TEXT = ("【合成开发材料，非真实票据】\n票据编号：SYN-REPAIR-001\n维修金额：3000.00元\n"
             "维修项目：更换测试车辆前部受损部件，核对原始维修明细、维修时间以及实际支付情况。\n"
             "备注：此材料仅用于开发测试，需要人工核对票据编号、原图、费用项目和案件关联，不证明真实性或法律证明力。")


class EvidenceReviewTests(unittest.TestCase):
    def setUp(self):
        self.payload = load_development_case().model_dump(mode="json")
        self.payload["facts"] = [self.payload["facts"][0], self.payload["facts"][2]]
        self.payload["evidence"] = []
        self.payload["claims"] = []

    def fact(self, key, field, value="明确的合成信息", kind="text", status="unconfirmed"):
        return {"fact_id": key, "field": field, "value_type": kind, "value": value,
                "provenance": "user_statement", "confirmation_status": status, "statement_refs": ["msg_001"]}

    def material(self, key, document_type="other", text=LONG_TEXT):
        return {"evidence_id": key, "file_id": "file_"+key, "file_version": "1", "file_name": key+".txt",
                "media_type": "text/plain", "document_type": document_type,
                "content_sha256": hashlib.sha256((key+text).encode()).hexdigest(),
                "material_refs": [{"file_id": "file_"+key, "file_version": "1", "span_start": 0,
                                   "span_end": len(text), "quote": text}]}

    def request(self, payload=None, deadline=None):
        snapshot = CaseSnapshot.model_validate(payload or self.payload)
        return {"context": {"request_id": "evidence-review", "case_id": snapshot.case_id,
                            "experiment_run_id": None, "deadline_at": deadline},
                "case_version": snapshot.case_version, "case_snapshot": snapshot}

    def medical_claim(self):
        self.payload["facts"].append(self.fact("f_med", "medical_amount", {"amount": "120.00", "currency": "CNY"}, "money"))
        self.payload["claims"].append({"claim_id": "claim_med", "kind": "medical_cost", "description": "合成医疗费请求",
            "amount": {"amount": "120.00", "currency": "CNY"}, "fact_ids": ["f_med"], "evidence_ids": []})
        return self.payload["claims"][-1]

    def test_medical_reminders_require_both_groups(self):
        self.medical_claim()
        report = check_evidence(self.request()).data
        gaps = [item for item in report.material_gaps if item.finding_id.startswith("claim_document_")]
        self.assertEqual(len(gaps), 2)
        self.assertTrue(any("医疗费用票据" in gap.message for gap in gaps))
        self.assertTrue(any("诊疗记录" in gap.message for gap in gaps))
        self.assertEqual(report.rule_profile, "traffic_accident_v2")
        self.assertTrue(all("不是法定材料清单" in gap.suggested_correction for gap in gaps))

    def test_one_acceptable_clinical_record_satisfies_group(self):
        claim = self.medical_claim()
        self.payload["evidence"] = [self.material("receipt", "medical_invoice"), self.material("record", "discharge_summary")]
        claim["evidence_ids"] = ["receipt", "record"]
        report = check_evidence(self.request()).data
        self.assertFalse(any(item.finding_id.startswith("claim_document_") for item in report.material_gaps))

    def test_existing_unlinked_receipt_is_suggested_before_reupload(self):
        self.medical_claim()
        self.payload["evidence"] = [self.material("receipt", "medical_invoice")]
        report = check_evidence(self.request()).data
        gap = next(item for item in report.material_gaps if "医疗费用票据" in item.message)
        self.assertIn("已有此类型材料", gap.message)
        self.assertEqual(gap.evidence_ids, ["receipt"])
        self.assertTrue(gap.material_refs)
        question = next(item for item in report.suggested_questions if item.reason == gap.message)
        self.assertEqual(question.evidence_ids, ["receipt"])
        self.assertEqual(self.payload["claims"][0]["evidence_ids"], [])

    def test_excluded_receipt_cannot_satisfy_or_be_offered(self):
        claim = self.medical_claim()
        material = self.material("receipt", "medical_invoice")
        material.update(status="excluded", exclusion=self.payload["facts"][1]["confirmation"])
        self.payload["evidence"] = [material]
        claim["evidence_ids"] = ["receipt"]
        report = check_evidence(self.request()).data
        gap = next(item for item in report.material_gaps if "医疗费用票据" in item.message)
        self.assertNotIn("已有此类型材料", gap.message)
        self.assertEqual(gap.evidence_ids, [])

    def test_withdrawn_medical_claim_triggers_no_material_reminders(self):
        self.medical_claim()["status"] = "withdrawn"
        self.assertEqual(check_evidence(self.request()).data.material_gaps, [])

    def test_medical_rules_are_scoped_to_traffic_profile(self):
        self.medical_claim()
        self.payload["dispute_type"] = "unknown_dispute"
        report = check_evidence(self.request()).data
        self.assertEqual(report.rule_profile, "generic_v1")
        self.assertFalse(any(item.finding_id.startswith("claim_document_") for item in report.material_gaps))
        self.assertTrue(any("未配置诉求类型medical_cost" in text for text in report.limitations))

    def test_unknown_claim_kind_is_explicitly_not_covered(self):
        self.medical_claim()["kind"] = "unconfigured_loss"
        report = check_evidence(self.request()).data
        self.assertFalse(any(item.finding_id.startswith("claim_document_") for item in report.material_gaps))
        self.assertTrue(any("unconfigured_loss" in text for text in report.limitations))

    def test_material_type_is_not_guessed_from_filename(self):
        claim = self.medical_claim()
        item = self.material("receipt", "other")
        item["file_name"] = "medical_invoice.pdf"
        self.payload["evidence"] = [item]
        claim["evidence_ids"] = ["receipt"]
        self.assertTrue(any("医疗费用票据" in item.message for item in check_evidence(self.request()).data.material_gaps))

    def test_unknown_placeholder_keeps_fact_id_for_followup(self):
        self.payload["facts"].append(self.fact("insurance_pending", "insurance", "待核对"))
        report = check_evidence(self.request()).data
        finding = next(item for item in report.missing_facts if item.affected_fields == ["insurance"])
        self.assertEqual(finding.fact_ids, ["insurance_pending"])
        self.assertTrue(any(question.fact_ids == ["insurance_pending"] for question in report.suggested_questions))

    def test_explicit_absence_is_not_replaced_with_unknown(self):
        self.payload["facts"].append(self.fact("insurance_none", "insurance", "未投保"))
        self.assertFalse(any(item.affected_fields == ["insurance"] for item in check_evidence(self.request()).data.missing_facts))

    def test_disputed_fact_is_not_usable_claim_support(self):
        self.medical_claim()
        self.payload["facts"][-1]["confirmation_status"] = "disputed"
        report = check_evidence(self.request()).data
        self.assertTrue(any(item.finding_id.startswith("claim_facts_") for item in report.material_gaps))

    def test_evidence_fact_links_are_checked_without_auto_repair(self):
        item = self.material("receipt")
        item["proves_fact_ids"] = ["f_date_user"]
        self.payload["evidence"] = [item]
        before = deepcopy(self.payload)
        report = check_evidence(self.request()).data
        finding = next(item for item in report.material_gaps if "双向关联" in item.message)
        self.assertEqual(finding.fact_ids, ["f_date_user"])
        self.assertEqual(finding.evidence_ids, ["receipt"])
        self.assertEqual(self.payload, before)

    def test_reverse_missing_link_is_also_reported(self):
        self.payload["evidence"] = [self.material("receipt")]
        self.payload["facts"][0]["evidence_ids"] = ["receipt"]
        self.assertTrue(any("双向关联" in item.message for item in check_evidence(self.request()).data.material_gaps))

    def test_disconnected_claim_fact_and_material_is_reported(self):
        claim = self.medical_claim()
        self.payload["evidence"] = [self.material("receipt", "medical_invoice")]
        claim["evidence_ids"] = ["receipt"]
        report = check_evidence(self.request()).data
        finding = next(item for item in report.material_gaps if item.finding_id.startswith("claim_disconnected_"))
        self.assertEqual(finding.fact_ids, ["f_med"])
        self.assertEqual(finding.evidence_ids, ["receipt"])

    def test_distinct_large_amounts_are_not_rounded_into_equality(self):
        for index, amount in enumerate(["123456789012345678901234567890.01", "123456789012345678901234567890.02"]):
            self.payload["facts"].append(self.fact("f_large_"+str(index), "same_amount", {"amount": amount, "currency": "CNY"}, "money"))
        report = check_evidence(self.request()).data
        self.assertTrue(any(item.affected_fields == ["same_amount"] for item in report.conflicts))

    def test_distinct_scoped_receipt_amounts_do_not_conflict(self):
        for index, amount in enumerate(["100.00", "200.00"]):
            self.payload["facts"].append(self.fact("f_amount_"+str(index), f"losses.file{index}.repair_amount",
                                                  {"amount": amount, "currency": "CNY"}, "money"))
        self.assertFalse(check_evidence(self.request()).data.conflicts)

    def test_invalid_rule_authority_and_duplicate_ids_are_rejected(self):
        raw = load_evidence_rules().model_dump()
        raw["authority"] = "statutory_requirements"
        with self.assertRaises(ValueError):
            EvidenceRules.model_validate(raw)
        raw = load_evidence_rules().model_dump()
        rules = raw["profiles"]["traffic_accident"]["claim_materials"]["medical_cost"]
        rules.append(deepcopy(rules[0]))
        with self.assertRaises(ValueError):
            EvidenceRules.model_validate(raw)

    def test_custom_rule_profile_can_be_injected(self):
        raw = load_evidence_rules().model_dump()
        raw["profiles"]["traffic_accident"]["profile_id"] = "custom_development_v1"
        raw["profiles"]["traffic_accident"]["required_fields"] = {"accident_location": "事故地点"}
        report = check_evidence(self.request(), rules=EvidenceRules.model_validate(raw)).data
        self.assertEqual(report.rule_profile, "custom_development_v1")
        self.assertEqual([item.affected_fields for item in report.missing_facts], [["accident_location"]])

    def similarity(self, texts, policy=None, kinds=None):
        items = [Evidence.model_validate(self.material(f"e{index}", (kinds or ["other"]*len(texts))[index], text))
                 for index, text in enumerate(texts)]
        mapping = {item.evidence_id: text for item, text in zip(items, texts)}
        return inspect_text_similarity(items, lambda item: mapping[item.evidence_id], policy=policy)

    def test_different_byte_text_layouts_are_candidates_not_confirmed_duplicates(self):
        findings, questions, checks, _ = self.similarity([LONG_TEXT, LONG_TEXT.replace("\n", "  \n")])
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].category, "duplicate")
        self.assertIn("相似度不是重复概率", findings[0].message)
        self.assertEqual(questions[0].field, "evidence.possible_duplicate")
        self.assertIn("saved_material_text_similarity_v1", checks)

    def test_minor_ocr_text_difference_can_be_flagged(self):
        findings, _, _, _ = self.similarity([LONG_TEXT, LONG_TEXT.replace("前部", "前郜")])
        self.assertEqual(len(findings), 1)
        self.assertIn("三字片段相似度", findings[0].message)

    def test_different_numbers_do_not_imply_duplicate_material(self):
        findings, _, checks, _ = self.similarity([LONG_TEXT, LONG_TEXT.replace("3000.00", "3100.00")])
        self.assertFalse(findings)
        self.assertTrue(checks)

    def test_equal_short_amount_text_is_insufficient_for_similarity(self):
        findings, _, checks, limits = self.similarity(["维修金额：3000.00元", "维修金额：3000.00元"])
        self.assertFalse(findings)
        self.assertFalse(checks)
        self.assertTrue(any("文字过少" in value for value in limits))

    def test_different_document_types_are_not_compared(self):
        findings, _, checks, _ = self.similarity([LONG_TEXT, LONG_TEXT], kinds=["repair_invoice", "medical_invoice"])
        self.assertFalse(findings)
        self.assertFalse(checks)

    def test_pair_limits_are_reported(self):
        findings, _, _, limits = self.similarity([LONG_TEXT]*3, policy=SimilarityPolicy(max_pairs=1))
        self.assertEqual(len(findings), 1)
        self.assertTrue(any("剩余材料组合未检查" in value for value in limits))

    def test_missing_reader_does_not_claim_similarity_was_checked(self):
        report = check_evidence(self.request()).data
        self.assertNotIn("saved_material_text_similarity_v1", report.checks_performed)
        self.assertTrue(any("未接入" in value for value in report.limitations))

    def test_unavailable_reader_reports_scope_without_inventing_candidates(self):
        self.payload["evidence"] = [self.material("e0"), self.material("e1")]
        def unavailable(case_id, item):
            raise FileNotFoundError("not in store")
        report = check_evidence(self.request(), text_reader=unavailable).data
        self.assertFalse(report.duplicates)
        self.assertTrue(any("无法核实" in value for value in report.limitations))

    def test_directory_warns_about_orphan_material_and_unknown_fact(self):
        self.payload["dispute_type"] = "generic"
        self.payload.update(stage="litigation", selected_route="litigation")
        self.payload["evidence"] = [self.material("orphan")]
        result = build_evidence_directory(self.request())
        self.assertEqual(result.status, "insufficient")
        self.assertEqual(result.data.entries[0].review_status, "needs_confirmation")
        self.assertTrue(result.data.entries[0].warnings)

    def test_directory_propagates_similarity_findings_to_both_entries(self):
        self.payload.update(stage="litigation", selected_route="litigation")
        self.payload["evidence"] = [self.material("a"), self.material("b")]
        result = build_evidence_directory(self.request(), text_reader=lambda case_id, item: LONG_TEXT)
        self.assertTrue(all(any("疑似同一材料" in warning for warning in entry.warnings) for entry in result.data.entries))
        self.assertTrue(any(item.finding_id.startswith("similar_") for item in result.data.supplementary_checklist))

    def test_expired_request_does_not_call_reader(self):
        def never(case_id, item):
            self.fail("reader must not be called")
        result = check_evidence(self.request(deadline=datetime.now(timezone.utc)-timedelta(seconds=1)), text_reader=never)
        self.assertEqual(result.error.code, "timeout")

    def test_material_reader_uses_saved_full_text_and_checks_integrity(self):
        with TemporaryDirectory() as directory:
            repo = SQLiteCaseRepository(Path(directory)/"cases.db")
            case = repo.create_case(CaseSnapshot.model_validate(self.payload))
            service = CaseService(repo)
            for file_id, text in [("a", LONG_TEXT), ("b", LONG_TEXT.replace("\n", "  \n"))]:
                service.assets.put_file(case_id=case.case_id, file_id=file_id, file_version="1", file_name=file_id,
                    media_type="text/plain", document_type="repair_invoice", content=text.encode())
            parsed = service.parse_documents({"context": self.request()["context"], "case_version": 1, "file_ids": ["a", "b"]})
            self.assertIsNotNone(parsed.data)
            saved = service.update_case({"context": self.request()["context"], "expected_case_version": 1,
                "changes": {"upsert_evidence": parsed.data.documents, "upsert_facts": parsed.data.extracted_facts},
                "change_reason": "store unconfirmed development candidates"})
            self.assertEqual(saved.status, "ok", saved.error)
            current = saved.data.case_snapshot
            report = service.check_evidence(self.request(current.model_dump(mode="json"))).data
            self.assertEqual(len(report.duplicates), 1)
            self.assertEqual(repo.get_case(case.case_id), current)
            item = current.evidence[0]
            self.assertEqual(read_material_text(service.assets, case.case_id, item), LONG_TEXT)
            with self.assertRaises(ValueError):
                read_material_text(service.assets, case.case_id, item.model_copy(update={"content_sha256": "f"*64}))
            bad_ref = item.material_refs[0].model_copy(update={"quote": "forged"})
            with self.assertRaises(ValueError):
                read_material_text(service.assets, case.case_id, item.model_copy(update={"material_refs": [bad_ref]}))
            with self.assertRaises(LookupError):
                read_material_text(service.assets, "another_case", item)


if __name__ == "__main__":
    unittest.main()
