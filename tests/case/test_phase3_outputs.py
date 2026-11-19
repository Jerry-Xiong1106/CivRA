"""Costs, route choices, drafting and verification using isolated case stores."""

from backend.case.intake.updates import set_case_stage, record_user_statement
import json
import sys
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from backend.case import CaseService, SQLiteCaseRepository
from scripts.case.fixtures import load_development_case
from backend.case.contracts import InvocationContext
from backend.case.intake.contracts import UpdateCaseRequest
from backend.case.drafting.documents import DocumentTemplate, TemplateRegistry, MeteredLLMDraftingGateway
from backend.case.drafting.templates import load_builtin_templates
from backend.case.models import BusinessOutput, CaseSnapshot, OutputLink, SourcePointer
from backend.case.review.verification import MeteredLLMReviewGateway

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"scripts/case"))
from demo_support import DEMO_REPLY, confirmed_demo_changes


class OutputsTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.repo=SQLiteCaseRepository(Path(self.temp.name)/"cases.db")
        self.case=self.repo.create_case(load_development_case())
        self.templates=TemplateRegistry()
        self.templates.register(DocumentTemplate(template_id="test-only",template_version="1",title="测试草稿",
            origin_kind="synthetic-test",source_refs=[],fields={"claimant_name":"原告姓名","defendant_name":"被告姓名"}))
        self.service=CaseService(self.repo,templates=self.templates)

    def context(self,request_id="test",expired=False):
        return InvocationContext(request_id=request_id,case_id=self.case.case_id,experiment_run_id=None,
            deadline_at=datetime.now(timezone.utc)-timedelta(seconds=1) if expired else None)

    def request(self,**extra):
        case=self.repo.get_case(self.case.case_id)
        return {"context":self.context(),"case_version":case.case_version,"case_snapshot":case,**extra}

    def calc(self,kind="sum_amounts",inputs=None,version="arithmetic_v1"):
        return self.service.calculate_costs({"context":self.context(),"case_version":self.repo.get_case(self.case.case_id).case_version,
            "calculation_type":kind,"rule_version":version,"inputs":inputs or {"amounts":[{"amount":"0.1","currency":"CNY"},{"amount":"0.2","currency":"CNY"}]}})

    def litigation(self):
        set_case_stage(self.repo, self.case.case_id,"litigation")

    def resolve(self):
        case=self.repo.get_case(self.case.case_id)
        record_user_statement(self.repo, case_id=case.case_id,expected_case_version=case.case_version,request_id="reply",
            message_id="msg_demo_reply",actor_id="synthetic_user",text=DEMO_REPLY,created_at=datetime.now(timezone.utc))
        case=self.repo.get_case(case.case_id)
        result=self.service.update_case(UpdateCaseRequest(context=self.context("resolve"),expected_case_version=case.case_version,
            changes=confirmed_demo_changes(case),change_reason="explicit test user reply"))
        self.assertEqual(result.status,"ok",result.error)

    def draft_request(self,**changes):
        return self.request(route="litigation",confirmed_fact_ids=["f_claimant"],template_id="test-only",template_version="1",source_refs=[],**changes)

    def output(self,**changes):
        case=self.repo.get_case(self.case.case_id)
        return BusinessOutput(output_id="candidate",output_version=1,case_id=case.case_id,case_version=case.case_version,
            output_type="analysis",text="测试输出",**changes)

    def test_decimal_addition_and_multiplication(self):
        self.assertEqual(self.calc().data.amounts["total"].amount,"0.3")
        result=self.calc("multiply",{"amount":{"amount":"3000.00","currency":"CNY"},"multiplier":"0.3"})
        self.assertEqual(result.data.amounts["total"].decimal,900)

    def test_unknown_rule_and_mixed_currencies_are_errors(self):
        self.assertEqual(self.calc(version="latest").status,"error")
        self.assertEqual(self.calc(inputs={"amounts":[{"amount":"1","currency":"USD"},{"amount":"1","currency":"CNY"}]}).status,"error")

    def test_missing_is_not_zero_and_float_is_not_money(self):
        for values in [[],[{"amount":1.2,"currency":"CNY"}]]:
            with self.subTest(values=values): self.assertEqual(self.calc(inputs={"amounts":values}).status,"error")

    def test_material_total_does_not_sum_unconfirmed_values(self):
        result=self.calc("materials_total",{"fact_ids":["f_repair_amount"]})
        self.assertEqual(result.status,"insufficient")
        self.assertEqual(result.data.amounts,{})

    def test_confirmed_material_total_and_excluded_duplicate(self):
        self.resolve()
        result=self.calc("materials_total",{"fact_ids":["f_repair_amount"]})
        self.assertEqual(result.status,"ok",result.error)
        self.assertEqual(result.data.amounts["total"].amount,"3000.00")
        duplicate=self.calc("materials_total",{"fact_ids":["f_repair_amount_copy"]})
        self.assertEqual(duplicate.status,"insufficient")

    def test_routes_are_parallel_choices_unknown_cost_is_not_zero(self):
        before=self.repo.get_case(self.case.case_id)
        result=self.service.compare_routes(self.request(available_authorities=[],calculations=[self.calc().data],user_goals=["节省成本"]))
        self.assertEqual(result.status,"insufficient")
        self.assertEqual({option.route for option in result.data.options},{"negotiation","mediation","litigation"})
        self.assertTrue(all(option.costs==[] for option in result.data.options))
        self.assertEqual(self.repo.get_case(self.case.case_id),before)
        self.assertTrue(result.output_refs)

    def test_workflow_requires_explicit_matching_selection(self):
        self.assertEqual(self.service.get_route_workflow(self.request(route="litigation")).status,"error")
        self.litigation()
        result=self.service.get_route_workflow(self.request(route="litigation"))
        self.assertTrue(result.data.steps)
        self.assertTrue(all(step.deadline is None for step in result.data.steps))
        self.assertTrue(all(step.completion_condition for step in result.data.steps))

    def test_timeline_preserves_conflicting_dates_and_graph_links(self):
        timeline=self.service.build_visualization(self.request(chart_type="timeline"))
        self.assertEqual({item["date"] for item in timeline.data.records},{"2026-05-12","2026-05-13"})
        graph=self.service.build_visualization({**self.request(chart_type="evidence_graph"),"context":self.context("graph")})
        ids={record["id"] for record in graph.data.records}
        self.assertTrue(all(link["from"] in ids and link["to"] in ids for link in graph.data.links))

    def test_forged_snapshot_same_version_rejected_by_stored_operations(self):
        case=self.case.model_dump(mode="json"); case["facts"][2]["value"]="forged confirmed name"
        request=self.request(chart_type="timeline"); request["case_snapshot"]=case
        self.assertEqual(self.service.build_visualization(request).error.code,"invalid_request")

    def test_draft_uses_selected_confirmed_facts_and_leaves_unknowns(self):
        self.litigation()
        result=self.service.draft_document(self.draft_request())
        self.assertEqual(result.status,"insufficient",result.error)
        self.assertIn("示例甲",result.data.text)
        self.assertIn("【待补充：被告姓名】",result.data.text)
        self.assertIn("confirmed_claims",result.data.missing_fields)
        self.assertNotIn("2026-05-13",result.data.text)
        artifact=self.service.assets.get_artifact(self.case.case_id,result.data.artifact_id)
        self.assertEqual(artifact["content"].decode(),result.data.text)

    def test_draft_replay_and_changed_request_id_rejected(self):
        self.litigation(); request=self.draft_request()
        first=self.service.draft_document(request)
        self.assertEqual(self.service.draft_document(request).data,first.data)
        request["confirmed_fact_ids"]=[]
        self.assertEqual(self.service.draft_document(request).status,"error")

    def test_draft_unknown_template_or_unconfigured_format_rejected(self):
        self.litigation()
        self.service.drafter.exporters.pop("pdf")
        for changes in [{"template_version":"latest"},{"export_formats":["pdf"]},{"confirmed_fact_ids":["f_date_user"]}]:
            with self.subTest(changes=changes):
                request=self.draft_request(); request.update(changes)
                self.assertEqual(self.service.draft_document(request).status,"error")

    def test_forged_confirmed_name_is_not_drafted(self):
        self.litigation()
        request=self.draft_request(); case=request["case_snapshot"].model_dump(mode="json")
        case["facts"][2]["value"]="forged name"; request["case_snapshot"]=case
        self.assertEqual(self.service.draft_document(request).status,"error")

    def test_no_semantic_gateway_never_reports_full_verification(self):
        result=self.service.verify_output(self.request(candidate_output=self.output(),cited_sources=[]))
        self.assertEqual(result.status,"insufficient")
        self.assertIn("semantic_fact_faithfulness",result.data.checks_not_performed)

    def test_verifier_reports_stale_unconfirmed_and_missing(self):
        self.litigation()
        output=self.output(links=[OutputLink(output_location="fact",fact_ids=["f_date_user"])],missing_fields=["court"])
        output=output.model_copy(update={"case_version":1})
        result=self.service.verify_output(self.request(candidate_output=output,cited_sources=[]))
        self.assertTrue({"version","conflict","required_field"}.issubset({item.category for item in result.data.findings}))

    def test_verifier_requires_source_text_identity_version(self):
        pointer=SourcePointer(source_id="law-test",source_version="1")
        self.service.verifier.source_resolver=lambda _:dict(source_id="law-test",version="2",text="test")
        result=self.service.verify_output(self.request(candidate_output=self.output(source_refs=[pointer]),cited_sources=[pointer]))
        self.assertTrue(any(item.category=="citation" for item in result.data.findings))

    def test_verifier_detects_changed_structured_fact_value(self):
        record=self.case.facts[2].model_dump(mode="json"); record["value"]="another name"
        result=self.service.verify_output(self.request(candidate_output=self.output(structured_data={"facts":[record]}),cited_sources=[]))
        self.assertTrue(any(item.output_location=="structured_data.facts" for item in result.data.findings))

    def test_metered_gateway_uses_supplied_model_and_rejects_invented_ids(self):
        calls=[]
        def call(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(content=json.dumps({"findings":[]}),model="test-double",usage=None,cost=None)
        self.service.verifier.semantic_gateway=MeteredLLMReviewGateway(call)
        result=self.service.verify_output(self.request(candidate_output=self.output(),cited_sources=[]))
        self.assertEqual(result.status,"ok")
        self.assertEqual(result.data.model_usage["model"],"test-double")
        self.assertEqual(calls[0]["temperature"],0)
        bad={"finding_id":"x","category":"conflict","severity":"warning","message":"test","fact_ids":["invented"]}
        self.service.verifier.semantic_gateway=MeteredLLMReviewGateway(lambda **_:
            SimpleNamespace(content=json.dumps({"findings":[bad]}),tool_calls=None))
        request=self.request(candidate_output=self.output(),cited_sources=[]); request["context"]=self.context("bad")
        result=self.service.verify_output(request)
        self.assertEqual(result.status,"insufficient")
        self.assertIn("semantic_review_failed",result.data.checks_not_performed)

    def test_expired_operations_do_not_save_outputs(self):
        request=self.request(chart_type="timeline"); request["context"]=self.context(expired=True)
        self.assertEqual(self.service.build_visualization(request).error.code,"timeout")
        request=self.request(candidate_output=self.output(),cited_sources=[]); request["context"]=self.context(expired=True)
        self.assertEqual(self.service.verify_output(request).error.code,"timeout")

    def test_three_profiles_have_separate_types_versions_and_source_links(self):
        registry=load_builtin_templates()
        self.assertEqual(len(registry.list_templates()),3)
        self.assertEqual(len(registry.list_templates("private_lending")),1)
        self.service.drafter.templates=registry
        self.litigation()
        request=self.draft_request(); request.update(template_id="cn_complaint_private_lending",template_version="civra-fields-v1")
        self.assertEqual(self.service.draft_document(request).status,"error")

    def test_grounded_drafting_gateway_and_replay_do_not_repeat_model_call(self):
        self.litigation()
        pointer=SourcePointer(source_id="test-law",source_version="v1")
        self.service.drafter.source_resolver=lambda _:dict(source_id="test-law",version="v1",source_type="law",
            title="Synthetic test law",locator="Article 1",text="Synthetic legal text for adapter tests only.")
        calls=[]
        def call(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(content=json.dumps({"paragraphs":[{"text":"测试理由段落，仅用于接口测试。",
                "fact_ids":["f_claimant"],"source_ids":["test-law"]}]}),model="test-double",usage=None,cost=None)
        self.service.drafter.drafting_gateway=MeteredLLMDraftingGateway(call)
        request=self.draft_request(); request["source_refs"]=[pointer]
        result=self.service.draft_document(request)
        self.assertEqual(result.status,"insufficient",result.error)
        self.assertEqual(result.data.structured_data["reasoning_state"],"generated_needs_verification")
        self.assertTrue(any(link.output_location=="reasoning:1" for link in result.data.links))
        self.service.draft_document(request)
        self.assertEqual(len(calls),1)

    def test_unknown_drafting_source_id_is_rejected(self):
        gateway=MeteredLLMDraftingGateway(lambda **_:SimpleNamespace(content=json.dumps({"paragraphs":[
            {"text":"bad","fact_ids":["f_claimant"],"source_ids":["invented"]}]})))
        with self.assertRaises(ValueError):
            gateway.compose(facts=[self.case.facts[2]],sources=[{"source_id":"test-law","text":"test"}],context=self.context())

    def test_semantic_verification_replay_does_not_repeat_model_call(self):
        calls=[]
        def call(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(content=json.dumps({"findings":[]}),model="test-double",usage=None,cost=None)
        self.service.verifier.semantic_gateway=MeteredLLMReviewGateway(call)
        request=self.request(candidate_output=self.output(),cited_sources=[])
        first=self.service.verify_output(request)
        second=self.service.verify_output(request)
        self.assertEqual(first,second)
        self.assertEqual(len(calls),1)

    def test_expired_storage_write_and_long_money_precision(self):
        with self.assertRaises(TimeoutError):
            self.service.assets.save_output(self.output(),deadline_at=datetime.now(timezone.utc)-timedelta(seconds=1))
        amount="9"*80+".01"
        total=self.calc(inputs={"amounts":[{"amount":amount,"currency":"CNY"},{"amount":"0.01","currency":"CNY"}]}).data.amounts["total"]
        self.assertEqual(total.amount,"9"*80+".02")

    def test_identical_materials_are_deduplicated_but_conflicting_amounts_block_total(self):
        for conflicting in [False,True]:
            with self.subTest(conflicting=conflicting):
                payload=load_development_case().model_dump(mode="json")
                payload["case_id"]="same-material-"+str(conflicting)
                confirmation=payload["facts"][2]["confirmation"]
                for index,field in [(3,"losses.a.repair_amount"),(4,"losses.b.repair_amount")]:
                    payload["facts"][index].update(field=field,confirmation_status="confirmed",confirmation=confirmation)
                if conflicting: payload["facts"][4]["value"]["amount"]="4000.00"
                self.case=self.repo.create_case(CaseSnapshot.model_validate(payload))
                result=self.calc("materials_total",{"fact_ids":["f_repair_amount","f_repair_amount_copy"]})
                if conflicting:
                    self.assertEqual(result.status,"insufficient")
                    self.assertEqual(result.data.amounts,{})
                else:
                    self.assertEqual(result.data.amounts["total"].amount,"3000.00")

    def test_calculation_verifier_replays_formula_and_reports_changed_amount(self):
        record=self.calc().data.model_dump(mode="json")
        record["amounts"]["total"]["amount"]="0.4"
        result=self.service.verify_output(self.request(candidate_output=self.output(structured_data={"calculations":[record]}),cited_sources=[]))
        self.assertIn("structured_calculation_replay",result.data.checks_performed)
        self.assertTrue(any(item.category=="calculation" for item in result.data.findings))
