from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from zipfile import ZipFile
import hashlib
import json
import unittest

from lxml import etree
from backend.case import CaseService, SQLiteCaseRepository, load_development_case
from backend.case.models import CaseSnapshot
from backend.case.drafting.official_forms import OfficialTemplateLibrary, W
from backend.tools.official_forms import OfficialFormToolClient
from backend.agent.case_adapter import RepositoryCasePort


class OfficialFormsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = SQLiteCaseRepository(Path(self.temp.name) / "case.db")
        self.service = CaseService(self.repo)
        self.forms = self.service.official_forms

    def create(self, kind="traffic_accident"):
        raw = load_development_case().model_dump(mode="json")
        raw.update(case_id="official_" + kind, dispute_type=kind, stage="litigation", selected_route="litigation")
        prototype = next(f for f in raw["facts"] if f["fact_id"] == "f_claimant")
        prototype.update(fact_id="f_form", field={"traffic_accident": "accident_details", "private_lending": "loan_agreement", "sales_contract": "contract_details"}[kind],
                         value="双方于2026年1月5日就相关事项形成书面记录。")
        raw.update(facts=[prototype], claims=[], evidence=[])
        case = self.repo.create_case(CaseSnapshot.model_validate(raw))
        self.case_id = case.case_id
        return case

    def request(self, request_id="select", **kwargs):
        return dict(context=dict(case_id=self.case_id, request_id=request_id, experiment_run_id=None, deadline_at=None),
                    case_version=self.repo.get_case(self.case_id).case_version, **kwargs)

    def prepare(self):
        result = self.forms.recommend(self.request())
        self.assertEqual(result.status, "ok", result.error)
        card = result.data["candidates"][0]
        request = self.request("preview", template_id=card["template_id"], template_version=card["version"])
        result = self.forms.prepare(request)
        self.assertNotEqual(result.status, "error", result.error)
        return result.data, request

    def test_lookup_pages_cover_all_blank_forms_and_expose_actual_drafting_ids(self):
        from backend.tools.official_forms import create_form_lookup
        tool = create_form_lookup(self.service)
        ids = set()
        fillable = []
        offset = 0
        while offset is not None:
            page = tool.invoke({"offset": offset})["data"]
            assert page["total"] == 113 and 1 <= len(page["candidates"]) <= 8
            for card in page["candidates"]:
                assert card["template_id"] not in ids
                ids.add(card["template_id"])
                if card["draft_document_args"]:
                    t = self.service.drafter.templates.get(**card["draft_document_args"])
                    fillable.append(t.template_id)
            offset = page["next_offset"]
        assert len(ids) == 113 and len(fillable) == 3
        miss = tool.invoke({"query": "不存在的模板", "document_type": "defense"})
        assert miss["status"] == "empty" and miss["data"]["candidates"] == []

    def confirm(self, preview, text=None):
        case = self.repo.get_case(self.case_id)
        reply = RepositoryCasePort(self.repo).record_user_reply(case_id=self.case_id, expected_case_version=case.case_version,
            request_id="confirm-reply", message_id="confirm-message", actor_id="test-user",
            text=text or preview.structured_data["confirmation_text"], created_at=datetime.now(timezone.utc))
        self.assertNotEqual(reply.status, "error", reply.error)
        return self.forms.confirm_and_export(self.request("export", preview_output_id=preview.output_id, confirmation_message_id="confirm-message"))

    def test_three_forms_fill_only_after_recorded_confirmation_and_keep_originals(self):
        for kind in ("traffic_accident", "private_lending", "sales_contract"):
            with self.subTest(kind=kind):
                self.create(kind)
                preview, _ = self.prepare()
                self.assertTrue(preview.structured_data["proposed_fields"])
                rejected = self.forms.confirm_and_export(self.request("bad", preview_output_id=preview.output_id, confirmation_message_id="missing"))
                self.assertEqual(rejected.status, "error")
                template, original = self.forms.library.get(preview.template_id, preview.template_version)
                result = self.confirm(preview)
                self.assertNotEqual(result.status, "error", result.error)
                artifact = self.service.assets.get_artifact(self.case_id, result.data.artifact_id)
                with ZipFile(BytesIO(original)) as before, ZipFile(BytesIO(artifact["content"])) as after:
                    self.assertIsNone(after.testzip())
                    for name in before.namelist():
                        if name != "word/document.xml":
                            self.assertEqual(before.read(name), after.read(name))
                    root = etree.fromstring(after.read("word/document.xml"))
                    table = root.find(W + "body").findall(W + "tbl")
                    for proposal in preview.structured_data["proposed_fields"]:
                        field = next(f for f in template["fields"] if f["field_id"] == proposal["field_id"])
                        cell = table[field["table"]].findall(W + "tr")[field["row"]].findall(W + "tc")[field["cell"]]
                        self.assertIn(proposal["text"], "\n".join("".join(p.itertext()) for p in cell.findall(W + "p")))
                self.assertEqual(hashlib.sha256(self.forms.library.get(preview.template_id, preview.template_version)[1]).hexdigest(), template["sha256"])
                retry = self.forms.confirm_and_export(self.request("export", preview_output_id=preview.output_id, confirmation_message_id="confirm-message"))
                self.assertEqual(retry.data, result.data)

    def test_examples_and_other_roles_not_selectable(self):
        self.create()
        records = self.forms.library.records()
        self.assertEqual(sum(r["kind"] == "blank" for r in records), 113)
        self.assertEqual(sum(r["kind"] == "example" for r in records), 113)
        self.assertEqual(self.forms.recommend(self.request(role="defendant")).data["candidates"], [])
        example = next(r for r in records if r["kind"] == "example")
        with self.assertRaises(ValueError):
            self.forms.library.get(example["template_id"], example["version"])

    def test_plain_agreement_does_not_approve_specific_preview(self):
        self.create()
        preview, _ = self.prepare()
        self.assertEqual(self.confirm(preview, "同意").status, "error")

    def test_changed_case_rejects_old_preview(self):
        self.create()
        preview, _ = self.prepare()
        case = self.repo.get_case(self.case_id)
        raw = case.model_dump(mode="json")
        raw["facts"][0]["value"] = "已修正的事实"
        raw["case_version"] += 1
        raw["updated_at"] = datetime.now(timezone.utc)
        self.repo.replace_case(CaseSnapshot.model_validate(raw), expected_case_version=case.case_version)
        self.assertEqual(self.confirm(preview).status, "error")

    def test_gateway_invalid_references_and_failures_are_not_saved(self):
        self.create()
        preview, request = self.prepare()
        field = preview.structured_data["template"]["fields"][0]["field_id"]
        request["context"]["request_id"] = "invalid-model"
        self.forms.gateway = SimpleNamespace(propose=lambda **kwargs: ([dict(field_id=field, text="错误内容", fact_ids=["made-up"])], {}))
        self.assertEqual(self.forms.prepare(request).status, "error")
        def fail(**kwargs):
            raise RuntimeError("provider offline")
        self.forms.gateway = SimpleNamespace(propose=fail)
        self.assertEqual(self.forms.prepare(request).status, "error")

    def test_prepare_retry_and_agent_boundary(self):
        self.create()
        preview, request = self.prepare()
        self.forms.gateway = SimpleNamespace(propose=lambda **kwargs: self.fail("retry must not call model"))
        self.assertEqual(self.forms.prepare(request).data, preview)
        tools = OfficialFormToolClient(self.forms)
        self.assertEqual(set(tools.contracts()), {"recommend_official_templates", "prepare_official_form"})
        result = tools.invoke("recommend_official_templates", context=request["context"], case_version=request["case_version"], arguments={})
        self.assertEqual(result.status, "ok")
        with self.assertRaises(ValueError):
            tools.invoke("confirm_and_export", context=request["context"], case_version=request["case_version"], arguments={})
        with self.assertRaises(ValueError):
            tools.invoke("prepare_official_form", context=request["context"], case_version=request["case_version"], arguments={"case_version": 999})


if __name__ == "__main__":
    unittest.main()
