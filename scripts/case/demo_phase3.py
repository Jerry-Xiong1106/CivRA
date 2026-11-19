"""Run real business operations with synthetic case data; no live LLM call.

Use --ocr-image for actual configured PaddleOCR inference. Without it the parser
reads an explicit UTF-8 text sample; no fake OCR result is substituted.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

from backend.case import CaseService, SQLiteCaseRepository
from backend.case.intake.updates import set_case_stage, record_user_statement  # noqa: E402
from scripts.case.fixtures import load_development_case
from backend.case.contracts import InvocationContext
from backend.case.operations import REQUEST_MODELS, RESULT_MODELS
from backend.case.materials.ocr import OcrEngineRegistry
from demo_support import DEMO_REPLY, confirmed_demo_changes


def run_demo(output_dir,ocr_image=None):
    output_dir=Path(output_dir)
    output_dir.mkdir(parents=True,exist_ok=True)
    database=output_dir/"cases.sqlite3"
    if database.exists():
        raise ValueError("demo database already exists; choose a new output directory")
    repo=SQLiteCaseRepository(database)
    case=repo.create_case(load_development_case())
    config=json.loads((ROOT/"data/config/member3_ocr.json").read_text(encoding="utf-8"))
    service=CaseService(repo,ocr=OcrEngineRegistry().create(config["engine"],config["options"]))
    results={}
    examples=output_dir/"calls"; examples.mkdir()

    def context(name):
        return InvocationContext(request_id="phase3-demo-"+name,case_id=case.case_id,experiment_run_id="synthetic-dev-only",deadline_at=None)

    def invoke(name,**values):
        current=repo.get_case(case.case_id)
        fields={"context":context(name)}
        model=REQUEST_MODELS[name]
        if "case_version" in model.model_fields: fields["case_version"]=current.case_version
        if "case_snapshot" in model.model_fields: fields["case_snapshot"]=current
        request=model.model_validate({**fields,**values})
        result=getattr(service,name)(request)
        RESULT_MODELS[name].model_validate(result.model_dump(mode="json"))
        payload={"synthetic_development_example":True,"request":request.model_dump(mode="json"),"result":result.model_dump(mode="json")}
        (examples/(name+".json")).write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        if result.status=="error": raise RuntimeError(f"{name}: {result.error.code}: {result.summary}")
        results[name]={"status":result.status,"summary":result.summary,"output_refs":result.output_refs}
        return result

    if ocr_image:
        from PIL import Image
        with Image.open(ocr_image) as image: media=Image.MIME[image.format]
        content=Path(ocr_image).read_bytes()
        filename=Path(ocr_image).name
    else:
        media,filename="text/plain","synthetic-new-material.txt"
        content="合成开发样例\n事故日期：2026-05-12\n维修金额：3000.00元\n".encode()
    service.assets.put_file(case_id=case.case_id,file_id="phase3-material",file_name=filename,media_type=media,
        document_type="other",content=content,file_version="1")
    parsed=invoke("parse_documents",file_ids=["phase3-material"])
    invoke("check_evidence")
    set_case_stage(repo, case.case_id,"litigation")  # Synthetic demo's explicit user choice.
    current=repo.get_case(case.case_id)
    reply=record_user_statement(repo, case_id=case.case_id,expected_case_version=current.case_version,
        request_id="phase3-demo-reply",message_id="msg_demo_reply",actor_id="synthetic_user",text=DEMO_REPLY,
        created_at=datetime.now(timezone.utc))
    if reply.status!="ok": raise RuntimeError(reply.summary)
    current=repo.get_case(case.case_id)
    invoke("update_case",expected_case_version=current.case_version,changes=confirmed_demo_changes(current),change_reason="explicit fixed synthetic reply")
    calculation=invoke("calculate_costs",calculation_type="materials_total",rule_version="arithmetic_v1",inputs={"fact_ids":["f_repair_amount"]})
    invoke("compare_routes",available_authorities=[],calculations=[calculation.data],user_goals=["了解三条路线的条件"])
    invoke("get_route_workflow",route="litigation")
    invoke("build_evidence_directory")
    current=repo.get_case(case.case_id)
    chosen=[fact.fact_id for fact in current.facts if fact.confirmation_status=="confirmed" and not fact.superseded_by]
    draft=invoke("draft_document",route="litigation",confirmed_fact_ids=chosen,template_id="cn_complaint_traffic_accident",
        template_version="civra-fields-v1",source_refs=[],export_formats=["md"])
    artifact=service.assets.get_artifact(case.case_id,draft.data.artifact_id)
    (output_dir/"complaint-filling-draft.md").write_bytes(artifact["content"])
    verification=invoke("verify_output",candidate_output=draft.data,cited_sources=draft.data.source_refs)
    invoke("build_visualization",chart_type="timeline")
    summary={"synthetic":True,"case_version":current.case_version,"operations":results,
        "history_versions":[item.case_version for item in repo.get_history(case.case_id)],
        "material_parsing_methods":sorted({segment.extraction_method for segment in parsed.data.text_segments}),
        "material_candidate_confirmations":sorted({fact.confirmation_status for fact in parsed.data.extracted_facts}),
        "total":calculation.data.amounts["total"].model_dump(mode="json"),
        "checks_not_performed":verification.data.checks_not_performed,
        "scope":"Real business operations on synthetic development data; no live LLM or legal accuracy evaluation."}
    (output_dir/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return summary


if __name__=="__main__":
    if hasattr(sys.stdout,"reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--ocr-image",type=Path)
    args=parser.parse_args()
    print(json.dumps(run_demo(args.output_dir,args.ocr_image),ensure_ascii=False,indent=2))
