"""Run the mock traffic-accident demo and print the run timeline.

    .venv/bin/python scripts/demo_mock_run.py

Uses mock tools and a scripted model, so no API key is needed. Records are
written to var/runs/<run_id>/ (trace.jsonl, events.jsonl, checkpoint.json).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.agent.case_port import InMemoryCaseStore  # noqa: E402
from backend.agent.llm import ScriptedLLM, tool_call  # noqa: E402
from backend.agent.runner import AgentRunner  # noqa: E402
from backend.agent.store import RunStore  # noqa: E402
from backend.common.schemas import UserReply  # noqa: E402
from backend.tools.mock_tools import build_mock_registry  # noqa: E402

CASE = "case_demo"


def main() -> None:
    cases = InMemoryCaseStore()
    cases.create(CASE, {"dispute_type": "traffic_accident", "facts": {"accident_date": "2026-05-12"}})
    llm = ScriptedLLM([
        tool_call("check_evidence"),
        tool_call("ask_user", questions=[
            {"question_id": "q1", "text": "交警是否出具了事故责任认定书？结论是什么？"},
            {"question_id": "q2", "text": "对方车辆是否投保了交强险和商业险？"},
        ]),
        tool_call("update_case", facts={"liability_finding": "对方全责", "insurance": "交强险+商业三者险100万"}),
        tool_call("check_evidence"),
        tool_call("search_statutes", query="机动车交通事故 交强险 赔偿顺序"),
        tool_call("finish", answer="可先向对方交强险保险人主张赔偿，不足部分再由商业险和侵权人承担。",
                  open_issues=["维修费发票尚未上传"]),
    ])
    runner = AgentRunner(llm, build_mock_registry(cases), cases, RunStore())

    info = runner.start_run(CASE, "我的车被追尾了，想要对方赔偿维修费。")
    print(f"[{info.status}] {info.run_id}")
    for q in info.wait.questions:
        print(f"  Agent 追问：{q.text}")

    info = runner.resume_run(info.run_id, UserReply(answers={"q1": "对方全责", "q2": "交强险+商业三者险100万"}),
                             wait_id=info.wait.wait_id)
    print(f"[{info.status}] {info.final.answer}")
    print(f"  未解决：{info.final.open_issues}")
    print(f"  引用：{[f'{s.title} {s.locator}' for s in info.final.source_refs]}")
    print(f"  用量：{info.usage.model_calls} 次模型调用，{info.usage.tool_calls} 次工具调用，"
          f"{info.usage.prompt_tokens + info.usage.completion_tokens} tokens")

    print("\n执行记录：")
    for s in runner.get_trace(info.run_id):
        name = s.tool_name or ""
        print(f"  #{s.step_id:<2} {s.step_type:<11} {name:<16} {s.status:<12} v{s.case_version}  {s.result_summary}")
    print(f"\n记录文件：var/runs/{info.run_id}/")


if __name__ == "__main__":
    main()
