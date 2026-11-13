"""New tools against the previously saved real Member 4 ToolRegistry."""

import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.case import CaseService,SQLiteCaseRepository,load_development_case,get_contract_manifest

try:
    from backend.tools.registry import ToolRegistry
    from backend.common.schemas import ToolContext
except ModuleNotFoundError:
    ToolRegistry=None


@unittest.skipIf(ToolRegistry is None,"requires the saved Member 4 runtime integration copy")
class Phase3RuntimeTests(unittest.TestCase):
    def setUp(self):
        from backend.tools.case_tools import register_case_tools
        self.temp=TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.repo=SQLiteCaseRepository(Path(self.temp.name)/"cases.db")
        self.case=self.repo.create_case(load_development_case())
        self.service=CaseService(self.repo)
        self.registry=register_case_tools(ToolRegistry(),self.service)

    def context(self,step=1):
        return ToolContext(case_id=self.case.case_id,run_id="phase3-test",step_id=step,case_version=1,
            deadline=datetime.now(timezone.utc)+timedelta(seconds=30),stage="consultation")

    def test_all_generated_contract_schemas_are_valid(self):
        from jsonschema import Draft202012Validator
        for schema in get_contract_manifest()["schemas"].values():
            Draft202012Validator.check_schema(schema)

    def test_new_tools_execute_real_business_service(self):
        args={"calculation_type":"sum_amounts","rule_version":"arithmetic_v1",
            "inputs":{"amounts":[{"amount":"0.1","currency":"CNY"},{"amount":"0.2","currency":"CNY"}]}}
        result=self.registry.execute("calculate_costs",args,self.context()).result
        self.assertEqual(result.status,"ok",result.error)
        self.assertEqual(result.data["data"]["amounts"]["total"]["amount"],"0.3")
        result=self.registry.execute("compare_routes",{"available_authorities":[],"calculations":[],"user_goals":[]},self.context(2)).result
        self.assertEqual(result.status,"insufficient",result.error)
        self.assertTrue(result.data["output_refs"])
        result=self.registry.execute("build_visualization",{"chart_type":"timeline"},self.context(3)).result
        self.assertEqual(result.status,"ok",result.error)

    def test_parse_uploaded_file_exposes_material_location_to_trace(self):
        self.service.assets.put_file(case_id=self.case.case_id,file_id="new-upload",file_name="new.txt",file_version="1",
            media_type="text/plain",document_type="other",content="维修金额：3000.00元".encode())
        result=self.registry.execute("parse_documents",{"file_ids":["new-upload"]},self.context()).result
        self.assertEqual(result.status,"ok",result.error)
        self.assertTrue(result.data["matches"])
        self.assertIn("extraction:", result.data["matches"][0]["source_ref"]["locator"]["chunk_id"])
        self.assertEqual(result.data["data"]["extracted_facts"][0]["confirmation_status"],"unconfirmed")

    def test_verify_saved_output_and_litigation_gate(self):
        visualization=self.registry.execute("build_visualization",{"chart_type":"timeline"},self.context()).result
        output_id=visualization.data["output_refs"][0]
        result=self.registry.execute("verify_output",{"output_id":output_id},self.context(2)).result
        self.assertEqual(result.status,"insufficient",result.error)
        self.assertTrue(result.data["data"]["checks_not_performed"])
        result=self.registry.execute("draft_document",{"route":"litigation","confirmed_fact_ids":["f_claimant"],
            "template_id":"cn_complaint_traffic_accident","template_version":"civra-fields-v1","source_refs":[]},self.context()).result
        self.assertEqual(result.error.code,"NOT_ALLOWED")

    def test_gateways_preserve_actual_runtime_usage_and_cost_models(self):
        import json
        from backend.common.schemas import LLMResponse,TokenUsage,CostInfo
        from backend.case.drafting import MeteredLLMDraftingGateway
        from backend.case.verification import MeteredLLMReviewGateway
        from backend.case.models import BusinessOutput
        from backend.case.contracts import InvocationContext
        response=LLMResponse(content=json.dumps({"paragraphs":[{"text":"接口测试",
            "fact_ids":["f_claimant"],"source_ids":["test-law"]}]}),model="offline-contract-test",
            usage=TokenUsage(prompt_tokens=10,completion_tokens=5,total_tokens=15),cost=CostInfo(amount="0.001"))
        context=InvocationContext(request_id="gateways",case_id=self.case.case_id,experiment_run_id=None,deadline_at=None)
        _,usage=MeteredLLMDraftingGateway(lambda **_:response).compose(facts=[self.case.facts[2]],
            sources=[{"source_id":"test-law","text":"synthetic test only"}],context=context)
        self.assertEqual(usage["usage"]["total_tokens"],15)
        self.assertEqual(usage["cost"]["amount"],"0.001")
        response=response.model_copy(update={"content":json.dumps({"findings":[]})})
        output=BusinessOutput(output_id="test",output_version=1,case_id=self.case.case_id,case_version=1,output_type="analysis",text="test")
        _,usage=MeteredLLMReviewGateway(lambda **_:response).review(output=output,case_snapshot=self.case,sources=[],context=context)
        self.assertEqual(usage["usage"]["total_tokens"],15)
