"""Draft export integration, trust boundaries and atomic storage failure tests."""
import hashlib
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend.case import CaseService, SQLiteCaseRepository, load_development_case
from backend.case.models import CaseSnapshot, SourcePointer
from backend.case.errors import VersionConflict
from backend.case.drafting.documents import MeteredLLMDraftingGateway
from backend.case.drafting.document_exports import DraftBlock, markdown, pdf, word
from backend.case.drafting.templates import CURRENT_VERSION, load_builtin_templates


class DraftExportTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.repo=SQLiteCaseRepository(Path(self.temp.name)/"cases.db")
        self.service=CaseService(self.repo)
        self.raw=load_development_case().model_dump(mode="json")
        self.raw.update(stage="litigation",selected_route="litigation")

    def request(self, *, fields=None, formats=None):
        case=self.repo.create_case(CaseSnapshot.model_validate(self.raw))
        return dict(context=dict(request_id="export-test",case_id=case.case_id,experiment_run_id=None,deadline_at=None),
            case_version=case.case_version,case_snapshot=case,route="litigation",
            template_id="cn_complaint_"+case.dispute_type,template_version=CURRENT_VERSION,
            confirmed_fact_ids=fields or ["f_claimant"],source_refs=[],export_formats=formats or ["md"])

    def source(self, **changes):
        raw=dict(source_id="test-law",version="v1",title="测试来源",locator="第一条",source_type="law",text="仅供测试的来源正文。")
        raw.update(changes)
        self.service.drafter.source_resolver=lambda _:raw
        return SourcePointer(source_id="test-law",source_version="v1")

    def gateway(self, paragraphs=None):
        paragraphs=paragraphs if paragraphs is not None else [{"text":"仅供测试的理由段。","fact_ids":["f_claimant"],"source_ids":["test-law"]}]
        gateway=SimpleNamespace(compose=lambda **_:(paragraphs,{"model":"test-double"}))
        self.service.drafter.drafting_gateway=gateway
        return gateway

    def assert_empty_storage(self):
        with self.repo.connection() as connection:
            for table in ("case_outputs","case_artifacts"):
                self.assertEqual(connection.execute("SELECT count(*) FROM "+table).fetchone()[0],0,table)

    def test_three_formats_same_content_hashes_and_chinese_font(self):
        from docx import Document
        from pypdf import PdfReader
        request=self.request(formats=["md","docx","pdf"])
        result=self.service.draft_document(request)
        self.assertEqual(result.status,"insufficient",result.error)
        data=result.data.structured_data
        self.assertEqual(set(data["artifacts"]),{"md","docx","pdf"})
        exports={k:self.service.assets.get_artifact(result.case_id,v)["content"] for k,v in data["artifacts"].items()}
        for name,content in exports.items():
            self.assertEqual(hashlib.sha256(content).hexdigest(),data["export_manifest"][name]["sha256"])
            self.assertEqual(len(content),data["export_manifest"][name]["size_bytes"])
        self.assertEqual(exports["md"].decode(),result.data.text)
        body="\n".join(p.text for p in Document(BytesIO(exports["docx"])).paragraphs)
        pages=PdfReader(BytesIO(exports["pdf"])).pages
        pdf_text="".join(page.extract_text() for page in pages)
        normalized=lambda text:"".join(text.split())
        for block in data["content_blocks"]:
            self.assertIn(block["text"],body)
            self.assertIn(normalized(block["text"]),normalized(pdf_text))
        self.assertGreater(len(pages),1)
        self.assertEqual(result.data.review_status,"needs_review")
        self.assertIn("电子送达",body)
        self.assertIn("signature_date",result.data.missing_fields)
        self.assertIn("signature",result.data.missing_fields)

    def test_all_profiles_and_historical_versions_are_available_and_isolated(self):
        registry=load_builtin_templates()
        self.assertEqual(len(registry.list_templates()),3)
        self.assertEqual(len(registry.list_templates(include_versions=True)),6)
        for item in registry.list_templates():
            self.assertEqual(item.template_version,CURRENT_VERSION)
            self.assertFalse(item.review_metadata["official_form_replica"])
            self.assertTrue(item.review_metadata["pdf_pages_1_based"])
            self.assertEqual(len(item.review_metadata["reference_sha256"]),64)
            registry.get(item.template_id,"civra-fields-v1")
            item.fields.clear()
            self.assertTrue(registry.get(item.template_id,CURRENT_VERSION).fields)

    def test_conflicted_confirmed_fact_is_withheld(self):
        confirmation=self.raw["facts"][2]["confirmation"]
        self.raw["facts"][0].update(confirmation_status="confirmed",confirmation=confirmation)
        result=self.service.draft_document(self.request(fields=["f_claimant",self.raw["facts"][0]["fact_id"]]))
        self.assertEqual(result.status,"insufficient",result.error)
        self.assertEqual(result.data.structured_data["withheld_fact_ids"],[self.raw["facts"][0]["fact_id"]])
        self.assertTrue(result.data.structured_data["fields"]["accident_date"].startswith("【待补充"))

    def test_confirmed_unknown_value_is_not_filled(self):
        self.raw["facts"][2]["value"]="未知"
        result=self.service.draft_document(self.request())
        self.assertIn("f_claimant",result.data.structured_data["withheld_fact_ids"])

    def test_unconfirmed_claim_not_included(self):
        result=self.service.draft_document(self.request())
        self.assertEqual(result.data.structured_data["claims"],[])
        self.assertIn("confirmed_claims",result.data.missing_fields)

    def test_model_forged_ids_and_malformed_output_fail_closed(self):
        request=self.request(); request["source_refs"]=[self.source()]
        for idx,paragraphs in enumerate([
            [{"text":"不应出现的段落","fact_ids":["invented"],"source_ids":["test-law"]}],
            [{"text":"不应出现的段落","fact_ids":["f_claimant"],"source_ids":["bad"]}],
            [{"text":"不应出现的段落"}],
            "invalid", [{"text":" ","fact_ids":["f_claimant"],"source_ids":["test-law"]}],
        ]):
            with self.subTest(idx=idx):
                request["context"]["request_id"]=f"invalid-{idx}"
                self.gateway(paragraphs)
                result=self.service.draft_document(request)
                self.assertEqual(result.status,"insufficient",result.error)
                self.assertEqual(result.data.structured_data["reasoning_state"],"generation_failed")
                self.assertNotIn("不应出现的段落",result.data.text)

    def test_bad_source_metadata_form_or_short_excerpt_never_reaches_model(self):
        request=self.request()
        gateway=self.gateway()
        for idx,changes in enumerate([dict(version="v2"),dict(title=""),dict(locator=""),dict(source_type="template"),dict(text_scope="short_excerpt_only")]):
            with self.subTest(changes=changes):
                request["context"]["request_id"]=f"source-{idx}"
                request["source_refs"]=[self.source(**changes)]
                with patch.object(gateway,"compose",side_effect=AssertionError("must not call")) as call:
                    result=self.service.draft_document(request)
                    self.assertEqual(result.status,"insufficient",result.error)
                    call.assert_not_called()
                self.assertEqual(result.data.structured_data["reasoning_state"],"insufficient_sources")
                self.assertTrue(result.data.structured_data["source_issues"])

    def test_gateway_timeout_does_not_save_partial_results(self):
        request=self.request(); request["source_refs"]=[self.source()]
        gateway=self.gateway()
        with patch.object(gateway,"compose",side_effect=TimeoutError):
            self.assertEqual(self.service.draft_document(request).error.code,"timeout")
        self.assert_empty_storage()

    def test_export_failure_does_not_save_any_artifact(self):
        request=self.request(formats=["md","docx","pdf"])
        self.service.drafter.exporters["pdf"]=lambda _:b"invalid PDF"
        self.assertEqual(self.service.draft_document(request).status,"error")
        self.assert_empty_storage()

    def test_storage_version_race_rolls_back_all_artifacts(self):
        request=self.request(formats=["md","docx"])
        with patch.object(self.service.assets,"_save_output",side_effect=VersionConflict(1,2)):
            self.assertEqual(self.service.draft_document(request).error.code,"version_conflict")
        self.assert_empty_storage()

    def test_storage_deadline_rolls_back_all_artifacts(self):
        request=self.request(formats=["md","docx"])
        with patch.object(self.service.assets,"_save_output",side_effect=TimeoutError):
            self.assertEqual(self.service.draft_document(request).error.code,"timeout")
        self.assert_empty_storage()

    def test_replay_does_not_export_again_and_different_formats_rejected(self):
        request=self.request()
        first=self.service.draft_document(request)
        self.service.drafter.exporters["md"]=lambda _:self.fail("re-exported")
        self.assertEqual(self.service.draft_document(request).data,first.data)
        request["export_formats"]=["docx"]
        self.assertEqual(self.service.draft_document(request).error.code,"invalid_request")

    def test_missing_pdf_font_is_actionable_and_atomic(self):
        request=self.request(formats=["md","pdf"])
        with patch.dict("os.environ",{"CIVRA_PDF_FONT_PATH":str(Path(self.temp.name)/"absent.ttf")}):
            result=self.service.draft_document(request)
        self.assertEqual(result.error.code,"invalid_request")
        self.assertIn("CIVRA_PDF_FONT_PATH",result.error.details["reason"])
        self.assert_empty_storage()

    def test_plain_text_and_markup_do_not_turn_into_links_or_html(self):
        text='<script>alert(1)</script> [点此](https://example.com) & **原文**'
        blocks=[DraftBlock(kind="title",text="测试"),DraftBlock(kind="paragraph",text=text)]
        from docx import Document
        from pypdf import PdfReader
        self.assertIn("&lt;script&gt;",markdown(blocks).decode())
        self.assertIn(r"\[点此\]",markdown(blocks).decode())
        self.assertEqual(Document(BytesIO(word(blocks))).paragraphs[1].text,text)
        self.assertIn("<script>",PdfReader(BytesIO(pdf(blocks))).pages[0].extract_text())

    def test_unsupported_control_character_is_rejected_before_persistence(self):
        self.raw["facts"][2]["value"]="姓名\x01"
        result=self.service.draft_document(self.request())
        self.assertEqual(result.status,"error")
        self.assert_empty_storage()

    def test_gateway_receives_only_selected_facts_and_confirmed_claims(self):
        request=self.request(); request["source_refs"]=[self.source()]
        calls=[]
        def call(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(content=json.dumps({"paragraphs":[]}),usage=None,cost=None)
        self.service.drafter.drafting_gateway=MeteredLLMDraftingGateway(call)
        result=self.service.draft_document(request)
        self.assertEqual(result.status,"insufficient",result.error)
        payload=json.loads(calls[0]["messages"][1]["content"])
        self.assertEqual([f["fact_id"] for f in payload["facts"]],["f_claimant"])
        self.assertEqual(payload["confirmed_claims"],[])
        self.assertEqual(payload["template"]["dispute_types"],["traffic_accident"])
