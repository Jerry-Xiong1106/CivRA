"""Synthetic review/correction, sample statistics, history and business activity demo."""
import argparse
from copy import deepcopy
from datetime import datetime,timezone
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from backend.case import CaseService, SQLiteCaseRepository, load_development_case
from backend.case.client import CaseToolClient
from backend.case.models import BusinessOutput, SourcePointer
from backend.case.runtime import RepositoryCasePort


def demo_source(pointer):
    # Deliberately synthetic annotations; never used as real legal authority.
    number="合成案甲" if pointer.source_id in {"sample-a","sample-a-copy"} else "合成案乙"
    fields=dict(case_number=number,decision_date="2025-01-02",court="合成法院",issue="合成维修金额争议",outcome="合成部分支持")
    if number=="合成案甲":fields.update(court_region="广东",accident_region="广西")
    return dict(source_id=pointer.source_id,version=pointer.source_version,source_type="case",title="合成统计样本",
        locator="合成段落",text="；".join(k+"："+v for k,v in fields.items()),court="合成法院",decision_date="2025-01-02",
        statistics={k:dict(value=v,quote=k+"："+v) for k,v in fields.items()})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    output=parser.parse_args().output.resolve()
    if output.exists() and any(output.iterdir()):raise SystemExit("output must be new or empty")
    output.mkdir(parents=True,exist_ok=True)
    repo=SQLiteCaseRepository(output/"cases.db");case=repo.create_case(load_development_case())
    service=CaseService(repo,source_resolver=demo_source);client=CaseToolClient(service)
    results={};checks=[]
    def request(name,**extra):
        current=repo.get_case(case.case_id)
        return dict(context=dict(case_id=case.case_id,request_id=name,experiment_run_id="synthetic-features10-13",deadline_at=None),
            case_version=current.case_version,**extra)
    def invoke(name,tool,arguments):
        result,receipt=client.invoke_recorded(tool,request(name,arguments=arguments),run_id="synthetic-run",step_id=name)
        assert result.status!="error",result.error
        assert receipt["recorded"]
        results[name]=result.model_dump(mode="json")
        return result
    calc_args=dict(calculation_type="sum_amounts",rule_version="arithmetic_v1",inputs=dict(amounts=[dict(amount="100",currency="CNY"),dict(amount="50",currency="CNY")]))
    computed=invoke("initial-calculation","calculate_costs",calc_args)
    bad_record=computed.data.model_dump(mode="json");bad_record["amounts"]["total"]["amount"]="999"
    candidate=BusinessOutput(case_id=case.case_id,case_version=1,output_id="synthetic-bad-calculation",output_version=1,
        output_type="analysis",text="合成计算候选，故意设错以演示核验",structured_data=dict(calculations=[bad_record]))
    service.assets.save_output(candidate)
    before=invoke("review-before","verify_output",dict(output_id=candidate.output_id))
    assert any(f.category=="calculation" for f in before.data.findings)
    plan=service.prepare_output_correction(request("correction-plan",verification_output_id=before.output_refs[0]))
    assert plan.status=="insufficient" and not plan.data.automatically_executed
    results["correction-plan"]=plan.model_dump(mode="json")
    repeated=invoke("recalculate","calculate_costs",calc_args)
    corrected=candidate.model_copy(update=dict(output_id="synthetic-corrected-calculation",text="按相同明确输入重新计算",
        structured_data=dict(calculations=[repeated.data.model_dump(mode="json")])))
    service.assets.save_output(corrected)
    after=invoke("review-after","verify_output",dict(output_id=corrected.output_id))
    assert not any(f.category=="calculation" for f in after.data.findings)
    assert after.data.checks_not_performed
    comparison=service.compare_verification_reports(request("review-comparison",verification_output_id=after.output_refs[0],
        previous_verification_output_id=before.output_refs[0]))
    assert comparison.data.previous_review["no_longer_reported"]
    assert service.assets.get_output(case.case_id,candidate.output_id)["output"]==candidate
    results["review-comparison"]=comparison.model_dump(mode="json")
    checks+=['wrong calculation detected','correction plan bound to saved target','recalculation clears arithmetic finding',
        'old output retained','semantic review still explicitly missing']
    invoke("timeline","build_visualization",dict(chart_type="timeline"))
    graph=invoke("graph","build_visualization",dict(chart_type="evidence_graph"))
    assert any(link["type"]=="claim_material" for link in graph.data.links)
    selected=[dict(source_ref=SourcePointer(source_id=s,source_version="v1"),expected_kind="case") for s in ['sample-a','sample-a-copy','sample-b']]
    statistics=service.build_case_statistics(request("statistics",case_snapshot=repo.get_case(case.case_id),selections=selected,group_by="court_region"))
    assert statistics.data.sample_size==2 and statistics.data.missing_values==1
    results["statistics"]=statistics.model_dump(mode="json")
    checks+=['graph direct material links','sample aliases deduplicated','unknown region retained']
    service.assets.put_file(case_id=case.case_id,file_id="supplement",file_name="合成补充.txt",media_type="text/plain",document_type="other",content="合成补充：收到部分款项，金额尚待核对。".encode())
    invoke("parse-supplement","parse_documents",dict(file_ids=["supplement"],ocr_mode="never"))
    reply=RepositoryCasePort(repo).record_user_reply(case_id=case.case_id,expected_case_version=1,request_id="synthetic-user-message",
        message_id="msg_supplement",actor_id="synthetic_user",text="我补充了模拟付款材料，金额还需核对。",created_at=datetime.now(timezone.utc))
    assert reply.status=="ok",reply.error
    service=CaseService(SQLiteCaseRepository(output/"cases.db"),source_resolver=demo_source)
    resumed=service.prepare_case_continuation(request("resume",since_version=1))
    assert resumed.data.unlinked_files and all(item["needs_review"] for item in resumed.data.outputs)
    assert resumed.data.case_snapshot.case_version==2
    results["resume"]=resumed.model_dump(mode="json")
    history=service.get_case_history(request("history"));assert len(history.data.items)==2
    assert repo.get_case(case.case_id,1)==case
    results["history"]=history.model_dump(mode="json")
    results["version-diff"]=service.compare_case_versions(request("version-diff",from_version=1,to_version=2)).model_dump(mode="json")
    activity=service.get_tool_activity(request("activity"));assert len(activity.data.events)==14
    assert all(event["run_id"]=="synthetic-run" and event["step_id"] for event in activity.data.events)
    results["activity"]=activity.model_dump(mode="json")
    checks+=['restart restores case version','new upload pending confirmation and evidence linkage','old outputs require review','history preserves old version',
        '14 start/finish events retained','runtime run/step correlation retained']
    (output/"results.json").write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf-8")
    summary=dict(synthetic=True,passed_checks=len(checks),checks=checks,case_version=2,tool_events=len(activity.data.events),
        limitation="No real LLM, legal correctness, shared Runtime or front-end acceptance claimed.")
    (output/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=="__main__":main()
