"""Real Member 3 tools + Member 4 AgentRunner; scripted model and synthetic user."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.case import CaseService, SQLiteCaseRepository
from backend.case.intake.updates import record_user_statement
from scripts.case.fixtures import load_development_case
from demo_support import DEMO_REPLY, confirmed_demo_changes  # noqa: E402


def run_agent_demo(directory):
    # Imports are delayed so standalone business code never requires Runtime dependencies.
    from backend.agent.model.llm import ScriptedLLM, tool_call
    from backend.agent.runner import AgentRunner
    from backend.agent.runtime.store import RunStore
    from backend.common.schemas import RunConfig, UserReply
    from backend.tools.case_tools import create_case_tools


    directory = Path(directory)
    repository = SQLiteCaseRepository(directory / "cases.sqlite3")
    original = repository.create_case(load_development_case())
    service = CaseService(repository)
    tools = create_case_tools(service)

    def ask_from_actual_report(messages, tools):
        report = json.loads(messages[-1]["content"])
        if report["status"] != "insufficient":
            raise AssertionError("expected the real evidence tool to find gaps")
        questions = report["data"]["data"]["suggested_questions"]
        return tool_call("ask_user", questions=[{key: question[key] for key in ("question_id", "text", "reason", "field")}
                                               for question in questions])

    def apply_recorded_reply(messages, tools):
        snapshot = repository.get_case(original.case_id)
        changes = confirmed_demo_changes(snapshot).model_dump(mode="json", exclude={"upsert_statements"})
        for fact in changes.get("upsert_facts", []):
            if fact.get("confirmation"):
                fact["confirmation"] = {"message_id": fact["confirmation"]["message_id"]}
        return tool_call("update_case", changes=changes,
            change_reason="应用记录合成用户回复后，按确认记录更新业务状态")

    def suggest_route_after_recheck(messages, tools):
        report = json.loads(messages[-1]["content"])
        if report["status"] != "ok":
            raise AssertionError("real checks must pass before the demo continues")
        return tool_call("finish", answer="材料已核对，可以在界面上选择「诉讼准备」继续。")

    llm = ScriptedLLM([
        tool_call("check_evidence"), ask_from_actual_report, apply_recorded_reply,
        tool_call("check_evidence"), suggest_route_after_recheck,
        tool_call("build_evidence_directory"),
        tool_call("finish", answer="已根据合成用户确认更新案件并生成证据目录草稿。",
                  open_issues=["证据真实性、法律证明力及法院具体材料要求仍需复核。"]),
    ])
    # This historical fixture groups four evidence findings in one scripted turn;
    # request that bounded batch explicitly, rather than bypass conversation policy.
    from backend.agent.case_adapter import RepositoryCasePort
    runner = AgentRunner(llm, tools, RepositoryCasePort(service), RunStore(directory / "runs"),
                         default_config=RunConfig(max_questions_per_round=4))
    info = runner.start_run(original.case_id, "请检查材料，问题确认后我希望准备诉讼。")
    if info.status != "waiting_user" or info.wait.kind != "clarification":
        raise AssertionError("Agent must pause on actual tool findings")
    questions = [item.model_dump(mode="json") for item in info.wait.questions]
    saved = record_user_statement(repository, case_id=original.case_id, expected_case_version=info.case_version,
        request_id="app-save-demo-reply", message_id="msg_demo_reply", actor_id="synthetic_user", text=DEMO_REPLY,
        created_at=datetime.now(timezone.utc))
    if saved.status != "ok":
        raise AssertionError(saved.summary)
    info = runner.resume_run(info.run_id, UserReply(text=DEMO_REPLY), wait_id=info.wait.wait_id,
        expected_case_version=saved.case_version, request_id="resume-confirmation")
    if info.status != "completed":
        raise AssertionError("Agent must finish the consultation after the recheck")
    # The synthetic user picks the litigation entry in the interface.
    consultation = info.run_id
    info = runner.select_route(original.case_id, "litigation", history_run_id=consultation, request_id="pick-route")
    trace = [step for run in (consultation, info.run_id) for step in runner.get_trace(run)]
    result = {"status": info.status, "run_id": info.run_id, "case_version": info.case_version,
        "questions_from_real_report": questions,
        "tool_steps": [{"name": step.tool_name, "status": step.status, "case_version": step.case_version}
                       for step in trace if step.step_type == "tool_call"],
        "history_versions": [item.case_version for item in repository.get_history(original.case_id)],
        "snapshot": repository.get_case(original.case_id).model_dump(mode="json"),
        "note": "业务工具及 Runtime 为真实代码，模型为 ScriptedLLM；不代表真实模型任务成功率。"}
    if info.status != "completed":
        raise AssertionError(result)
    return result


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, help="NEW result directory; use ignored var/ for persistent demo output")
    args = parser.parse_args()
    if args.output_dir:
        result = run_agent_demo(args.output_dir)
    else:
        with TemporaryDirectory() as directory:
            result = run_agent_demo(directory)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
