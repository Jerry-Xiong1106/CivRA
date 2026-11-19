"""Real text/PDF/Word parsing, immutable assets and replaceable OCR boundaries."""

from backend.case.intake.updates import set_case_stage
import io
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.case import CaseService, SQLiteCaseRepository
from scripts.case.fixtures import load_development_case
from backend.case.storage.assets import SQLiteAssetStore
from backend.case.contracts import InvocationContext
from backend.case.materials.contracts import ParseDocumentsRequest
from backend.case.intake.contracts import UpdateCaseRequest, CaseChanges
from backend.case.models import BusinessOutput
from backend.case.materials.ocr import OcrEngineRegistry, OcrLine, OcrPage, PaddleOcrConfig, PaddleOcrEngine
from backend.case.errors import CaseNotFound, VersionConflict


class OfflineEngine:
    """Explicit offline test double; never used for actual OCR validation."""
    def __init__(self):
        self.calls=0

    def recognize(self, content):
        self.calls+=1
        return OcrPage(engine="test-double",engine_version="1",model_version="fixture-only",lines=[
            OcrLine(text="事故日期：2026年5月12日",confidence=.99,polygon=[[0,0],[300,0],[300,30],[0,30]]),
            OcrLine(text="维修金额：3,000.00元",confidence=.60,polygon=[[0,40],[300,40],[300,70],[0,70]])])


class DocumentsAssetsTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo=SQLiteCaseRepository(Path(self.temp.name)/"cases.db")
        self.case=self.repo.create_case(load_development_case())
        self.engine=OfflineEngine()
        self.service=CaseService(self.repo,ocr=self.engine)
        self.assets=self.service.assets

    def put(self, file_id="uploaded", content=b"hello", media="text/plain", version="1"):
        return self.assets.put_file(case_id=self.case.case_id,file_id=file_id,file_version=version,
            file_name=file_id,document_type="other",media_type=media,content=content)

    def parse(self,files=None,mode="auto",version=1,deadline=None):
        return self.service.parse_documents(ParseDocumentsRequest(context=InvocationContext(request_id="parse",
            case_id=self.case.case_id,experiment_run_id=None,deadline_at=deadline),case_version=version,
            file_ids=files or ["uploaded"],ocr_mode=mode))

    def image(self):
        from PIL import Image
        data=io.BytesIO()
        Image.new("RGB",(400,100),"white").save(data,format="PNG")
        return data.getvalue()

    def test_text_preserves_quotes_and_produces_only_unconfirmed_facts(self):
        self.put(content="事故日期：2026年5月12日\n维修金额：3,000.00元\n责任认定：待核对".encode())
        result=self.parse()
        self.assertEqual(result.status,"ok",result.error)
        self.assertEqual(len(result.data.extracted_facts),3)
        values={fact.field:fact.value for fact in result.data.extracted_facts}
        self.assertEqual(values["accident_date"],"2026-05-12")
        self.assertEqual(values["losses.uploaded.repair_amount"]["amount"],"3000.00")
        for fact in result.data.extracted_facts:
            self.assertEqual(fact.confirmation_status,"unconfirmed")
            self.assertEqual(self.assets.read_extraction(self.case.case_id,fact.material_refs[0]),fact.material_refs[0].quote)
        self.assertEqual(self.repo.get_case(self.case.case_id),self.case)
        self.assertEqual(self.engine.calls,0)

    def test_bad_calendar_is_not_extracted_as_valid_date(self):
        self.put(content="事故日期：2026年2月30日".encode())
        self.assertFalse(self.parse().data.extracted_facts)

    def test_switched_engine_keeps_scores_boxes_and_locations(self):
        self.put(content=self.image(),media="image/png")
        result=self.parse()
        self.assertEqual(result.status,"insufficient")
        self.assertEqual(result.data.text_segments[1].confidence,.60)
        self.assertTrue(result.data.text_segments[0].material_ref.polygon)
        self.assertTrue(any("低分" in warning for warning in result.data.warnings))
        for segment in result.data.text_segments:
            self.assertEqual(self.assets.read_extraction(self.case.case_id,segment.material_ref),segment.text)

    def test_image_with_ocr_disabled_returns_error(self):
        self.put(content=self.image(),media="image/png")
        self.assertEqual(self.parse(mode="never").status,"error")
        self.assertEqual(self.engine.calls,0)

    def test_real_pdf_text_layer_avoids_ocr(self):
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, NameObject, DictionaryObject
        writer=PdfWriter()
        page=writer.add_blank_page(width=300,height=200)
        font=DictionaryObject({NameObject("/Type"):NameObject("/Font"),NameObject("/Subtype"):NameObject("/Type1"),NameObject("/BaseFont"):NameObject("/Helvetica")})
        page[NameObject("/Resources")]=DictionaryObject({NameObject("/Font"):DictionaryObject({NameObject("/F1"):writer._add_object(font)})})
        stream=DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 10 100 Td (Invoice 3000.00) Tj ET")
        page[NameObject("/Contents")]=writer._add_object(stream)
        buffer=io.BytesIO(); writer.write(buffer)
        self.put(content=buffer.getvalue(),media="application/pdf")
        result=self.parse()
        self.assertIn("3000.00",result.data.text_segments[0].text)
        self.assertEqual(result.data.text_segments[0].material_ref.page,1)
        self.assertEqual(self.engine.calls,0)
        self.assertTrue(result.data.warnings)

    def test_scanned_pdf_renders_before_engine_call(self):
        from pypdf import PdfWriter
        writer=PdfWriter(); writer.add_blank_page(width=300,height=200)
        buffer=io.BytesIO(); writer.write(buffer)
        self.put(content=buffer.getvalue(),media="application/pdf")
        result=self.parse()
        self.assertEqual(self.engine.calls,1)
        self.assertEqual(result.data.text_segments[0].extraction_method,"ocr")

    def test_real_word_paragraph_and_table_parse(self):
        from docx import Document
        doc=Document(); doc.add_paragraph("事故日期：2026-05-12")
        doc.add_table(rows=1,cols=1).cell(0,0).text="维修金额：3000.00元"
        buffer=io.BytesIO(); doc.save(buffer)
        self.put(content=buffer.getvalue(),media="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        self.assertEqual(len(self.parse().data.extracted_facts),2)

    def test_malformed_file_does_not_hide_other_success(self):
        self.put(file_id="broken",media="application/pdf",content=b"not pdf")
        self.put(content="维修金额：12.00元".encode())
        result=self.parse(["broken","uploaded"])
        self.assertEqual(result.status,"insufficient")
        self.assertEqual(len(result.data.documents),1)
        self.assertTrue(any("broken" in warning for warning in result.data.warnings))

    def test_stale_case_and_deadline_do_not_run_engine(self):
        self.put(content=self.image(),media="image/png")
        self.assertEqual(self.parse(version=2).error.code,"version_conflict")
        self.assertEqual(self.parse(deadline=datetime.now(timezone.utc)-timedelta(seconds=1)).status,"error")
        self.assertEqual(self.engine.calls,0)

    def test_file_versions_immutable_and_ambiguous_is_error(self):
        self.put()
        self.put()
        with self.assertRaises(ValueError): self.put(content=b"different")
        self.put(version="2",content=b"different")
        with self.assertRaises(ValueError): self.assets.get_file(self.case.case_id,"uploaded")
        self.assertEqual(self.parse().status,"error")
        result=self.service.parse_documents({"context":InvocationContext(request_id="versioned",case_id=self.case.case_id,
            experiment_run_id=None,deadline_at=None),"case_version":1,"file_ids":["uploaded"],"file_versions":{"uploaded":"2"}})
        self.assertEqual(result.data.documents[0].file_version,"2")

    def test_case_file_and_artifact_isolation(self):
        self.put()
        artifact=self.assets.put_artifact(self.case.case_id,"a.md","text/markdown",b"hello")
        for operation in [lambda:self.assets.get_file("another","uploaded"),lambda:self.assets.get_artifact("another",artifact)]:
            with self.assertRaises(CaseNotFound): operation()

    def test_extraction_version_span_quote_cannot_be_forged(self):
        self.put(content="维修金额：12.00元".encode())
        ref=self.parse().data.text_segments[0].material_ref
        for changes in [{"quote":"other"},{"span_end":9999},{"extraction_version":"other"}]:
            with self.subTest(changes=changes),self.assertRaises((ValueError,CaseNotFound)):
                self.assets.read_extraction(self.case.case_id,ref.model_copy(update=changes))

    def test_output_immutability_versions_and_staleness(self):
        output=BusinessOutput(output_id="test",output_version=1,case_id=self.case.case_id,case_version=1,
            output_type="visualization",text="test")
        self.assets.save_output(output); self.assets.save_output(output)
        with self.assertRaises(ValueError): self.assets.save_output(output.model_copy(update={"text":"changed"}))
        with self.assertRaises(ValueError): self.assets.save_output(output.model_copy(update={"output_version":3}))
        self.assets.save_output(output.model_copy(update={"output_version":2,"text":"new"}))
        self.assertEqual(self.assets.get_output(self.case.case_id,"test")["output"].output_version,2)
        set_case_stage(self.repo, self.case.case_id,"litigation")
        self.assertTrue(self.assets.get_output(self.case.case_id,"test")["stale"])
        with self.assertRaises(VersionConflict): self.assets.save_output(output)

    def test_registry_switch_and_unknown_engine_rejection(self):
        registry=OcrEngineRegistry()
        registry.register("test",lambda options:self.engine)
        self.assertIs(registry.create("test",{}),self.engine)
        with self.assertRaises(ValueError): registry.create("missing",{})
        with self.assertRaises(ValueError): registry.register("test",lambda options:self.engine)

    def test_paddle_v3_result_adapter_and_bgr_order(self):
        import numpy as np
        class Client:
            def predict(_,pixels):
                self.assertEqual(pixels[0,0].tolist(),[0,0,255])
                return [{"res":{"rec_texts":["打印 123"],"rec_scores":[.99],"rec_polys":[[[0,0],[10,0],[10,10],[0,10]]]}}]
        from PIL import Image
        buffer=io.BytesIO(); Image.new("RGB",(20,20),"red").save(buffer,format="PNG")
        engine=PaddleOcrEngine(PaddleOcrConfig(ocr_version="PP-OCRv6"),client=Client())
        self.assertEqual(engine.recognize(buffer.getvalue()).lines[0].text,"打印 123")

    def test_malformed_paddle_result_fails_explicitly(self):
        class Client:
            def predict(_,pixels): return [{"rec_texts":["x"],"rec_scores":[],"rec_polys":[]}]
        engine=PaddleOcrEngine(PaddleOcrConfig(ocr_version="PP-OCRv6"),client=Client())
        with self.assertRaises(RuntimeError): engine.recognize(self.image())

    def test_parse_candidates_can_be_saved_together_without_auto_confirmation(self):
        self.put(content="维修金额：3000.00元".encode())
        parsed=self.parse().data
        result=self.service.update_case(UpdateCaseRequest(context=InvocationContext(request_id="save-candidates",
            case_id=self.case.case_id,experiment_run_id=None,deadline_at=None),expected_case_version=1,
            changes=CaseChanges(upsert_evidence=parsed.documents,upsert_facts=parsed.extracted_facts),
            change_reason="save document-record candidates; user confirmation still required"))
        self.assertEqual(result.status,"ok",result.error)
        self.assertEqual(result.case_version,2)
        fact=next(fact for fact in result.data.case_snapshot.facts if fact.fact_id==parsed.extracted_facts[0].fact_id)
        self.assertEqual(fact.confirmation_status,"unconfirmed")
        self.assertEqual(self.assets.read_extraction(self.case.case_id,fact.material_refs[0]),"3000.00")

    def test_update_cannot_forge_uploaded_evidence_identity(self):
        self.put(content="维修金额：3000.00元".encode())
        parsed=self.parse().data
        for changes in [{"content_sha256":"f"*64},{"file_id":"not-uploaded"}]:
            with self.subTest(changes=changes):
                evidence=parsed.documents[0].model_dump(mode="json")
                evidence.update(changes)
                if "file_id" in changes:
                    for ref in evidence["material_refs"]: ref["file_id"]=changes["file_id"]
                result=self.service.update_case({"context":InvocationContext(request_id="forged",case_id=self.case.case_id,
                    experiment_run_id=None,deadline_at=None),"expected_case_version":1,"change_reason":"test",
                    "changes":{"upsert_evidence":[evidence]}})
                self.assertEqual(result.error.code,"invalid_request")
                self.assertEqual(self.repo.get_case(self.case.case_id).case_version,1)
