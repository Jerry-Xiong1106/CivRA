"""Source fidelity and failure handling; OCR doubles do not measure recognition accuracy."""

import io
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.case import CaseService, SQLiteCaseRepository
from scripts.case.fixtures import load_development_case
from backend.case.materials.ocr import OcrLine, OcrPage


class PageEngine:
    def __init__(self, pages):
        self.pages, self.calls = pages, 0

    def recognize(self, content):
        item = self.pages[self.calls]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return OcrPage(engine="test-double", engine_version="1", model_version="not-real-ocr", lines=[
            OcrLine(text=text, confidence=.99, polygon=[[0, i*40], [400, i*40], [400, i*40+30], [0, i*40+30]])
            for i, text in enumerate(item)])


class DocumentFieldTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = SQLiteCaseRepository(Path(self.temp.name) / "cases.db")
        self.case = self.repo.create_case(load_development_case())
        self.service = CaseService(self.repo, ocr=None)

    def put(self, content, media="text/plain", file_id="new", service=None):
        return (service or self.service).assets.put_file(
            case_id=self.case.case_id, file_id=file_id, file_version="1", file_name=file_id,
            media_type=media, document_type="other", content=content.encode() if isinstance(content, str) else content)

    def parse(self, service=None, mode="auto", deadline=None):
        return (service or self.service).parse_documents({
            "context": {"request_id": "new-fields", "case_id": self.case.case_id,
                        "experiment_run_id": None, "deadline_at": deadline},
            "case_version": 1, "file_ids": ["new"], "ocr_mode": mode})

    def pdf(self, sizes=((300, 200),), first_text=False):
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, NameObject, DictionaryObject
        writer = PdfWriter()
        for width, height in sizes:
            writer.add_blank_page(width=width, height=height)
        if first_text:
            font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
            writer.pages[0][NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
                DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
            stream = DecodedStreamObject()
            stream.set_data(b"BT /F1 12 Tf 10 100 Td (Printed invoice 1234) Tj ET")
            writer.pages[0][NameObject("/Contents")] = writer._add_object(stream)
        output = io.BytesIO()
        writer.write(output)
        return output.getvalue()

    def assert_sources(self, result):
        for fact in result.data.extracted_facts:
            self.assertEqual(fact.confirmation_status, "unconfirmed")
            self.assertEqual(fact.provenance, "document_record")
            for ref in fact.material_refs:
                self.assertEqual(self.service.assets.read_extraction(self.case.case_id, ref), ref.quote)
        self.assertEqual(self.repo.get_case(self.case.case_id), self.case)

    def test_ten_explicit_fields_have_exact_sources(self):
        self.put("事故发生日期：2026年5月12日\n事故地点：测试路口\n原告姓名：甲某\n被告名称：测试公司\n"
                 "保险关系：保单待核对\n责任认定：待核对\n维修费用：3,000.00元\n医疗费：人民币100元\n拖车费：50元\n鉴定费：80元")
        result = self.parse()
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(result.data.extracted_facts), 10)
        values = {fact.field: fact.value for fact in result.data.extracted_facts}
        self.assertEqual(values["accident_location"], "测试路口")
        self.assertEqual(values["claimant_name"], "甲某")
        self.assertEqual(values["defendant_name"], "测试公司")
        self.assertEqual(values["losses.new.towing_amount"], {"amount": "50", "currency": "CNY"})
        self.assert_sources(result)

    def test_fullwidth_values_normalize_without_changing_quotes(self):
        self.put("事故日期：２０２６／０５／１２\n维修金额：￥３，０００．５０元\n原告：Ａ某")
        result = self.parse()
        values = {fact.field: fact for fact in result.data.extracted_facts}
        self.assertEqual(values["accident_date"].value, "2026-05-12")
        self.assertEqual(values["losses.new.repair_amount"].value["amount"], "3000.50")
        self.assertEqual(values["losses.new.repair_amount"].material_refs[0].quote, "３，０００．５０")
        self.assertEqual(values["claimant_name"].value, "Ａ某")
        self.assert_sources(result)

    def test_invalid_amounts_never_become_partial_numbers(self):
        from backend.case.materials.field_extraction import extract_labelled_fields
        self.put("维修费：1元")
        result = self.parse()
        segment = result.data.text_segments[0]
        record, _ = self.service.assets.get_file(self.case.case_id, "new")
        for bad in ["1,2元", "12,34.00元", "3,0000元", "12.345元", "1..2元", "-100元", "100-200元",
                    "100万元", "100美元", "$100", "100%", "100/200元", "约100元", "100元或200元", "100元（估算）"]:
            with self.subTest(bad=bad):
                text = "维修费：" + bad
                candidate = segment.model_copy(update={"text": text, "material_ref": segment.material_ref.model_copy(
                    update={"span_end": len(text), "quote": text})})
                facts, warnings = extract_labelled_fields(record, "e_test", [candidate])
                self.assertFalse(facts)
                self.assertTrue(warnings)

    def test_invalid_date_has_visible_warning_and_original_text(self):
        self.put("事故日期：2026年2月30日\n维修费：12.00元")
        result = self.parse()
        self.assertEqual(result.status, "insufficient")
        self.assertEqual(len(result.data.extracted_facts), 1)
        self.assertIn("2026年2月30日", result.data.text_segments[0].text)
        self.assertTrue(any("事故日期" in warning for warning in result.data.documents[0].warnings))

    def test_date_with_time_or_range_is_not_silently_shortened(self):
        self.put("事故日期：2026-05-12至2026-05-13\n事故发生日期：2026-05-12 10:00")
        result = self.parse()
        self.assertFalse(result.data.extracted_facts)
        self.assertEqual(len(result.data.warnings), 2)

    def test_unlabelled_roles_and_unscoped_amounts_are_not_guessed(self):
        self.put("驾驶人姓名：甲某\n收款人：乙某\n付款100元\n总医疗费：200元")
        self.assertFalse(self.parse().data.extracted_facts)

    def test_inline_labelled_fields_keep_separate_values(self):
        self.put("原告：甲某 被告：乙某；维修费：12.50元；医疗费：20元")
        result = self.parse()
        values = {fact.field: fact.value for fact in result.data.extracted_facts}
        self.assertEqual(values["claimant_name"], "甲某")
        self.assertEqual(values["defendant_name"], "乙某")
        self.assertEqual(len(values), 4)
        self.assert_sources(result)

    def test_empty_label_does_not_steal_next_field(self):
        self.put("事故日期：\n维修费：20元")
        result = self.parse()
        self.assertEqual([fact.field for fact in result.data.extracted_facts], ["losses.new.repair_amount"])
        self.assertTrue(any("字段值为空" in warning for warning in result.data.warnings))

    def test_split_ocr_label_value_uses_value_polygon(self):
        engine = PageEngine([["维修金额：", "３，０００．５０元", "事故日期：", "2026-05-12"]])
        service = CaseService(self.repo, ocr=engine)
        self.put(self.pdf(), "application/pdf")
        result = self.parse(service)
        self.assertEqual(len(result.data.extracted_facts), 2)
        amount = result.data.extracted_facts[0]
        self.assertEqual(amount.value["amount"], "3000.50")
        self.assertEqual(amount.material_refs[0].polygon, result.data.text_segments[1].material_ref.polygon)
        self.assert_sources(result)

    def test_label_value_never_join_across_pdf_pages(self):
        engine = PageEngine([["维修金额："], ["3000元"]])
        service = CaseService(self.repo, ocr=engine)
        self.put(self.pdf(((300, 200), (300, 200))), "application/pdf")
        result = self.parse(service)
        self.assertEqual(engine.calls, 2)
        self.assertFalse(result.data.extracted_facts)

    def test_reparse_is_stable_and_keeps_conflicting_candidates(self):
        self.put("事故日期：2026-05-12\n事故日期：2026-05-13")
        first, second = self.parse(), self.parse()
        self.assertEqual(first.data.extracted_facts, second.data.extracted_facts)
        self.assertEqual({fact.value for fact in first.data.extracted_facts}, {"2026-05-12", "2026-05-13"})
        self.assert_sources(first)

    def test_identical_pdf_pages_keep_distinct_candidate_locations(self):
        engine = PageEngine([["维修费：20元"], ["维修费：20元"]])
        self.put(self.pdf(((300, 200), (300, 200))), "application/pdf")
        service = CaseService(self.repo, ocr=engine)
        result = self.parse(service)
        facts = result.data.extracted_facts
        self.assertEqual(len({fact.fact_id for fact in facts}), 2)
        self.assertEqual({fact.material_refs[0].page for fact in facts}, {1, 2})
        saved = service.update_case({"context": {"request_id": "save-pages", "case_id": self.case.case_id,
            "experiment_run_id": None, "deadline_at": None}, "expected_case_version": 1,
            "changes": {"upsert_evidence": result.data.documents, "upsert_facts": facts},
            "change_reason": "retain both original page locations"})
        self.assertEqual(saved.status, "ok", saved.error)

    def test_word_preserves_interleaved_tables_and_two_column_fields(self):
        from docx import Document
        doc = Document()
        doc.add_paragraph("段落一")
        table = doc.add_table(rows=1, cols=2)
        table.cell(0, 0).text, table.cell(0, 1).text = "维修费", "120.00元"
        doc.add_paragraph("段落二")
        doc.add_table(rows=1, cols=1).cell(0, 0).text = "事故日期：2026-05-12"
        buffer = io.BytesIO()
        doc.save(buffer)
        self.put(buffer.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        result = self.parse()
        self.assertEqual(result.data.text_segments[0].text, "段落一\n维修费\t120.00元\n段落二\n事故日期：2026-05-12")
        self.assertEqual(len(result.data.extracted_facts), 2)
        self.assert_sources(result)

    def test_pdf_page_failure_keeps_later_success_and_its_page_number(self):
        engine = PageEngine([RuntimeError("synthetic page failure"), ["维修费：20元"]])
        self.put(self.pdf(((300, 200), (300, 200))), "application/pdf")
        result = self.parse(CaseService(self.repo, ocr=engine))
        self.assertEqual(result.status, "insufficient")
        self.assertEqual(result.data.extracted_facts[0].material_refs[0].page, 2)
        self.assertTrue(any("第1页" in warning and "未完成" in warning for warning in result.data.documents[0].warnings))
        self.assert_sources(result)

    def test_empty_pdf_page_is_visible_when_ocr_disabled(self):
        self.put(self.pdf(((300, 200), (300, 200)), first_text=True), "application/pdf")
        result = self.parse(mode="never")
        self.assertEqual(len(result.data.text_segments), 1)
        self.assertTrue(any("第2页" in warning and "OCR 已关闭" in warning for warning in result.data.warnings))

    def test_empty_ocr_result_is_visible_beside_success(self):
        engine = PageEngine([[], ["维修费：20元"]])
        self.put(self.pdf(((300, 200), (300, 200))), "application/pdf")
        result = self.parse(CaseService(self.repo, ocr=engine))
        self.assertTrue(any("第1页" in warning and "未提取到文字" in warning for warning in result.data.warnings))
        self.assertEqual(len(result.data.extracted_facts), 1)

    def test_oversized_pdf_page_is_rejected_before_rasterization(self):
        engine = PageEngine([["维修费：20元"]])
        self.put(self.pdf(((20000, 20000), (300, 200))), "application/pdf")
        result = self.parse(CaseService(self.repo, ocr=engine))
        self.assertEqual(engine.calls, 1)
        self.assertEqual(result.data.extracted_facts[0].material_refs[0].page, 2)
        self.assertTrue(any("30 megapixel" in warning for warning in result.data.warnings))

    def test_always_mode_does_not_read_pdf_text_layer(self):
        engine = PageEngine([["维修费：20元"]])
        self.put(self.pdf(first_text=True), "application/pdf")
        with patch("pypdf._page.PageObject.extract_text", side_effect=AssertionError("must not read text")) as reader:
            result = self.parse(CaseService(self.repo, ocr=engine), mode="always")
        reader.assert_not_called()
        self.assertEqual(len(result.data.extracted_facts), 1)

    def test_auto_mode_falls_back_when_text_layer_extraction_fails(self):
        engine = PageEngine([["维修费：20元"]])
        self.put(self.pdf(first_text=True), "application/pdf")
        with patch("pypdf._page.PageObject.extract_text", side_effect=ValueError("broken text layer")):
            result = self.parse(CaseService(self.repo, ocr=engine))
        self.assertEqual(len(result.data.extracted_facts), 1)
        self.assertTrue(any("文字层读取失败" in warning for warning in result.data.warnings))

    def test_pdf_deadline_preserves_completed_pages(self):
        engine = PageEngine([["维修费：20元"], ["医疗费：30元"]])
        self.put(self.pdf(((300, 200), (300, 200))), "application/pdf")
        now = datetime.now(timezone.utc)
        with patch("backend.case.materials.documents.datetime") as clock:
            clock.now.side_effect = [now, now, now + timedelta(seconds=2)]
            result = self.parse(CaseService(self.repo, ocr=engine), deadline=now + timedelta(seconds=1))
        self.assertEqual(engine.calls, 1)
        self.assertEqual(len(result.data.extracted_facts), 1)
        self.assertTrue(any("第2页起" in warning for warning in result.data.warnings))


if __name__ == "__main__":
    unittest.main()
