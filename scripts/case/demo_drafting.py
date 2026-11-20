"""Export three synthetic complaint drafts; no real law/model or personal information."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from backend.case import CaseService, SQLiteCaseRepository
from scripts.case.fixtures import load_development_case
from backend.case.models import CaseSnapshot
from backend.case.drafting.templates import CURRENT_VERSION


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    output=parser.parse_args().output.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit("output must be new or empty")
    output.mkdir(parents=True,exist_ok=True)
    repo=SQLiteCaseRepository(output/"cases.db")
    service=CaseService(repo)
    manifest={}
    scenarios=[
        ("traffic_accident","accident_details","2026年5月12日，示例甲的车辆与示例乙的车辆发生碰撞，车辆维修支出为3000元。责任情况尚待核对。","repair_amount","请求赔偿维修支出", "repair_cost"),
        ("private_lending","loan_agreement","示例甲陈述曾向示例乙出借3000元；交付、到期和还款情况仍待核对。","loan_principal","请求返还借款本金","loan_principal"),
        ("sales_contract","contract_details","示例甲陈述向示例乙购买一件设备，已支付3000元；交付与解除条件仍待核对。","payment_amount","请求返还已支付价款","payment_refund"),
    ]
    for kind,field,detail,moneyfield,claim,claimkind in scenarios:
        raw=load_development_case().model_dump(mode="json")
        raw.update(case_id="synthetic_draft_"+kind,dispute_type=kind,stage="litigation",selected_route="litigation")
        statement="以下为合成演示，不是真实案件。我是示例甲，对方为示例乙。我确认本次输入："+detail+f"我选择诉讼，并确认请求为{claim}3000元。"
        raw["statements"][0]["text"]=statement
        seed=raw["facts"][2]
        facts=[]
        for fid,ff,value in [("f_claimant","claimant_name","示例甲"),("f_defendant","defendant_name","示例乙"),
                ("f_details",field,detail),("f_amount",moneyfield,{"amount":"3000.00","currency":"CNY"})]:
            fact=deepcopy(seed)
            fact.update(fact_id=fid,field=ff,value=value,value_type="money" if isinstance(value,dict) else "text")
            if fid in {"f_details","f_amount"}:
                fact["evidence_ids"]=["e_demo"]
            facts.append(fact)
        content=statement.encode("utf-8")
        evidence=deepcopy(raw["evidence"][0])
        evidence.update(evidence_id="e_demo",file_id="file_demo",file_name="合成陈述材料.txt",document_type="user_statement",
            content_sha256=hashlib.sha256(content).hexdigest(),proves_fact_ids=["f_details","f_amount"])
        evidence["material_refs"]=[dict(file_id="file_demo",file_version="1",paragraph="text",quote=statement)]
        raw.update(facts=facts,evidence=[evidence],claims=[dict(claim_id="claim_demo",kind=claimkind,
            description=claim,amount=dict(amount="3000.00",currency="CNY"),status="user_confirmed",
            fact_ids=["f_details","f_amount"],evidence_ids=["e_demo"])])
        case=repo.create_case(CaseSnapshot.model_validate(raw))
        service.assets.put_file(case_id=case.case_id,file_id="file_demo",file_name="合成陈述材料.txt",
            media_type="text/plain",document_type="user_statement",content=content)
        result=service.draft_document(dict(context=dict(request_id="demo-draft",case_id=case.case_id,
            experiment_run_id="synthetic-feature09",deadline_at=None),case_version=case.case_version,case_snapshot=case,
            confirmed_fact_ids=[f.fact_id for f in case.facts],route="litigation",template_id="cn_complaint_"+kind,
            template_version=CURRENT_VERSION,source_refs=[],export_formats=["md","docx","pdf"]))
        if result.status=="error":
            raise RuntimeError(result.error)
        for format_name,artifact_id in result.data.structured_data["artifacts"].items():
            artifact=service.assets.get_artifact(case.case_id,artifact_id)
            (output/(kind+"."+format_name)).write_bytes(artifact["content"])
        (output/(kind+".json")).write_text(result.model_dump_json(indent=2),encoding="utf-8")
        manifest[kind]=result.data.structured_data["export_manifest"]
    (output/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"output":str(output),"profiles":list(manifest),"formats":["md","docx","pdf"]},ensure_ascii=False))


if __name__=="__main__":
    main()
