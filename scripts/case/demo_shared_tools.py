"""Compare real Agent and fixed-sequence tool calls on isolated synthetic cases."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.case import CaseService, CaseToolClient, SQLiteCaseRepository, ToolInvocation, load_development_case
from backend.case.contracts import InvocationContext
from backend.case.models import CaseSnapshot
from backend.case.runtime import RepositoryCasePort
from demo_support import DEMO_REPLY, confirmed_demo_changes


def create_environment(directory, mode):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    repo = SQLiteCaseRepository(directory / "cases.sqlite3")
    payload = load_development_case().model_dump(mode="json")
    payload["case_id"] = "phase4_" + mode
    case = repo.create_case(CaseSnapshot.model_validate(payload))
    service = CaseService(repo)
    service.assets.put_file(
        case_id=case.case_id, file_id="shared_material", file_version="1", file_name="synthetic.txt",
        document_type="other", media_type="text/plain", content="事故日期：2026-05-12\n维修金额：3000.00元\n".encode(),
    )
    return repo, service, case.case_id


def save_reply(repo, case_id):
    case = repo.get_case(case_id)
    result = RepositoryCasePort(repo).record_user_reply(
        case_id=case_id, expected_case_version=case.case_version, request_id="shared-demo-reply",
        message_id="msg_demo_reply", actor_id="synthetic_user", text=DEMO_REPLY,
        created_at=datetime(2026, 11, 2, 8, tzinfo=timezone.utc),
    )
    if result.status != "ok":
        raise AssertionError(result.summary)


def update_arguments(repo, case_id):
    case = repo.get_case(case_id)
    return {
        "expected_case_version": case.case_version,
        "changes": confirmed_demo_changes(case).model_dump(mode="json", exclude={"upsert_statements"}),
        "change_reason": "apply explicit fixed synthetic reply",
    }


def draft_arguments(repo, case_id):
    case = repo.get_case(case_id)
    return {
        "route": "litigation", "template_id": "cn_complaint_traffic_accident",
        "template_version": "civra-fields-v1", "source_refs": [],
        "confirmed_fact_ids": [fact.fact_id for fact in case.facts
                               if fact.confirmation_status == "confirmed" and not fact.superseded_by],
    }


CALCULATION_ARGUMENTS = {
    "calculation_type": "materials_total", "rule_version": "arithmetic_v1", "inputs": {"fact_ids": ["f_repair_amount"]},
}


def run_fixed(directory):
    repo, service, case_id = create_environment(directory, "fixed")
    client = CaseToolClient(service)
    steps = []

    def call(name, arguments=None):
        case = repo.get_case(case_id)
        invocation = ToolInvocation(
            context=InvocationContext(request_id=f"fixed-{len(steps)}-{name}", case_id=case_id,
                                      experiment_run_id="phase4-fixed", deadline_at=None),
            case_version=case.case_version, arguments=arguments or {},
        )
        response = client.evaluate(name, invocation)
        if response["status"] != "ok":
            raise AssertionError(response)
        result = response["data"]
        steps.append({"name": name, "result": result})
        return result

    call("parse_documents", {"file_ids": ["shared_material"]})
    call("check_evidence")
    save_reply(repo, case_id)
    call("update_case", update_arguments(repo, case_id))
    call("check_evidence")
    calculation = call("calculate_costs", CALCULATION_ARGUMENTS)
    call("compare_routes", {"available_authorities": [], "calculations": [calculation["data"]], "user_goals": []})
    RepositoryCasePort(repo).set_stage(case_id, "litigation")  # Fixed synthetic user's explicit route choice.
    call("get_route_workflow", {"route": "litigation"})
    call("build_evidence_directory")
    draft = call("draft_document", draft_arguments(repo, case_id))
    call("verify_output", {"output_id": draft["data"]["output_id"]})
    call("build_visualization", {"chart_type": "timeline"})
    return {"steps": steps, "snapshot": repo.get_case(case_id).model_dump(mode="json")}


def run_agent(directory):
    from backend.agent.llm import ScriptedLLM, tool_call
    from backend.agent.runner import AgentRunner
    from backend.agent.store import RunStore
    from backend.common.schemas import RunConfig, UserReply
    from backend.tools.case_tools import register_case_tools
    from backend.tools.registry import ToolRegistry

    repo, service, case_id = create_environment(directory, "agent")
    registry = register_case_tools(ToolRegistry(), service)

    def last_business_result(messages):
        return json.loads(messages[-1]["content"])["data"]

    def ask_from_report(messages, tools):
        questions = last_business_result(messages)["data"]["suggested_questions"]
        return tool_call("ask_user", questions=[
            {key: question[key] for key in ("question_id", "text", "field", "reason")}
            for question in questions
        ])

    def update_from_reply(messages, tools):
        return tool_call("update_case", **update_arguments(repo, case_id))

    def compare_from_calculation(messages, tools):
        calculation = last_business_result(messages)["data"]
        return tool_call("compare_routes", available_authorities=[], calculations=[calculation], user_goals=[])

    def ask_route(messages, tools):
        comparison = last_business_result(messages)["summary"]
        return tool_call("request_route_choice", comparison=comparison,
                         options=[{"route": "litigation", "label": "诉讼准备"}])

    def draft_from_confirmed_facts(messages, tools):
        return tool_call("draft_document", **draft_arguments(repo, case_id))

    def verify_last_draft(messages, tools):
        output_id = last_business_result(messages)["data"]["output_id"]
        return tool_call("verify_output", output_id=output_id)

    script = [
        tool_call("parse_documents", file_ids=["shared_material"]),
        tool_call("check_evidence"), ask_from_report, update_from_reply,
        tool_call("check_evidence"), tool_call("calculate_costs", **CALCULATION_ARGUMENTS),
        compare_from_calculation, ask_route, tool_call("get_route_workflow", route="litigation"),
        tool_call("build_evidence_directory"), draft_from_confirmed_facts, verify_last_draft,
        tool_call("build_visualization", chart_type="timeline"),
        tool_call("finish", answer="合成业务接入演示完成。",
                  open_issues=["原表字段待校对，语义模型待接入，费用及导出选型保留。"]),
    ]
    runner = AgentRunner(ScriptedLLM(script), registry, RepositoryCasePort(repo),
                         RunStore(Path(directory) / "runs"), default_config=RunConfig(max_steps=60))
    info = runner.start_run(case_id, "请核对材料，确认后比较路线并准备诉讼草稿。", experiment_run_id="phase4-agent")
    if info.status != "waiting_user" or info.wait.kind != "clarification":
        raise AssertionError(info)
    save_reply(repo, case_id)
    case = repo.get_case(case_id)
    info = runner.resume_run(info.run_id, UserReply(text=DEMO_REPLY), wait_id=info.wait.wait_id,
                             expected_case_version=case.case_version, request_id="shared-demo-confirmation")
    if info.status != "waiting_user" or info.wait.kind != "route_selection":
        raise AssertionError(info)
    info = runner.resume_run(info.run_id, UserReply(route_choice="litigation"), wait_id=info.wait.wait_id,
                             expected_case_version=info.case_version, request_id="shared-demo-route")
    if info.status != "completed":
        raise AssertionError(info)
    steps = []
    for step in runner.get_trace(info.run_id):
        if step.step_type != "tool_call":
            continue
        record = runner.get_step_result(info.run_id, step.result_ref)
        result = record["result"]
        if result["status"] == "error":
            raise AssertionError(record)
        steps.append({"name": step.tool_name, "result": result["data"]})
    return {"steps": steps, "snapshot": repo.get_case(case_id).model_dump(mode="json"), "run_status": info.status}


def semantic_view(value):
    # Identity and receipt times differ because the two runs are isolated.
    ignored = {"case_id", "created_at", "updated_at", "confirmed_at", "output_id", "output_refs",
               "artifact_id", "artifacts", "request_digest", "_request_digest", "_business_result",
               "case_basis_hash", "plan_basis_hash", "target_digest", "target_output_id", "filename"}
    if isinstance(value, dict):
        return {key: semantic_view(item) for key, item in value.items() if key not in ignored}
    if isinstance(value, list):
        return [semantic_view(item) for item in value]
    return value


def run_shared_demo(directory):
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("choose a fresh output directory")
    directory.mkdir(parents=True, exist_ok=True)
    fixed = run_fixed(directory / "fixed")
    agent = run_agent(directory / "agent")
    equivalent = semantic_view(fixed["steps"]) == semantic_view(agent["steps"])
    snapshots_equal = semantic_view(fixed["snapshot"]) == semantic_view(agent["snapshot"])
    summary = {
        "scope": "synthetic integration using real business tools and ScriptedLLM",
        "agent_status": agent["run_status"], "business_outputs_equivalent": equivalent,
        "case_snapshots_equivalent": snapshots_equal,
        "tools_exercised": sorted({step["name"] for step in fixed["steps"]}),
        "tool_steps_per_mode": len(fixed["steps"]),
        "case_versions": {"fixed": fixed["snapshot"]["case_version"], "agent": agent["snapshot"]["case_version"]},
    }
    for name, value in [("fixed", fixed), ("agent", agent), ("summary", summary)]:
        (directory / (name + ".json")).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not equivalent or not snapshots_equal:
        raise AssertionError(summary)
    return summary


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.output_dir:
        summary = run_shared_demo(args.output_dir)
    else:
        with TemporaryDirectory() as directory:
            summary = run_shared_demo(directory)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
