"""Talk to the Agent in the terminal: it may ask you questions, you answer, it continues.

    .venv/bin/python scripts/agent/run_agent.py --message "我的车被追尾了，想要对方赔偿维修费"
    .venv/bin/python scripts/agent/run_agent.py --dry-run --mock-statutes  # fully offline

Needs SILICONFLOW_API_KEY in the repo's .env (never committed) for real models
and uncached query embeddings. Default retrieval uses vector_metadata.npz;
--dry-run only replaces the model, while --mock-statutes also replaces retrieval.
The CLI uses the in-memory demonstration setup. Applications can pass
case_service to backend.agent.factory.build_runner for persistent business tools.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.common.config import load_dotenv
from evaluation.config import run_config_from_pilot
from backend.agent.factory import MissingAPIKey, build_runner  # noqa: E402
from backend.agent.model.llm import ScriptedLLM, tool_call  # noqa: E402
from backend.agent.runner import AgentRunner  # noqa: E402
from backend.common.schemas import RunConfig, RunInfo, TraceStep, UserReply, Wait  # noqa: E402
from backend.knowledge.config import DEFAULT_MODE, RetrievalConfig, keyword_index_path, vector_index_path  # noqa: E402



def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parse_args()
    config = RunConfig(conversation_route="case_work" if args.dry_run else args.conversation_route)
    if args.pilot_config:
        config = run_config_from_pilot(json.loads(args.pilot_config.read_text(encoding="utf-8")))
        if args.dry_run or args.conversation_route != "auto":
            config = config.model_copy(update={"conversation_route": "case_work" if args.dry_run else args.conversation_route})
    if args.model:
        config = config.model_copy(update={"model": args.model})

    load_dotenv()
    retrieval_config = RetrievalConfig.from_mode(os.environ.get("CIVRA_RETRIEVAL_MODE", DEFAULT_MODE))
    required_indexes = []
    if retrieval_config.channel in ("vector", "hybrid"):
        required_indexes.append(vector_index_path(retrieval_config.variant))
    if retrieval_config.channel in ("keyword", "hybrid"):
        required_indexes.append(keyword_index_path("metadata" if retrieval_config.channel == "hybrid" else retrieval_config.variant))
    real_statutes = not args.mock_statutes and all(path.exists() for path in required_indexes)
    if not args.mock_statutes and not real_statutes:
        missing = ", ".join(str(path) for path in required_indexes if not path.exists())
        print(f"提示：当前检索模式缺少索引：{missing}，search_statutes 改用 mock。请运行对应的向量/BM25构建脚本。\n")
    try:
        setup = build_runner(llm=dry_run_llm() if args.dry_run else None, config=config,
                             real_statutes=real_statutes, mock_business_tools=not args.no_mock_tools)
    except MissingAPIKey as e:
        print(e)
        return 2

    facts = dict(f.split("=", 1) for f in args.fact)
    summary = {"facts": facts} | ({"dispute_type": args.dispute_type} if args.dispute_type else {})
    case_id = f"cli-{time.strftime('%Y%m%d-%H%M%S')}"  # one case per invocation, so runs never collide
    setup.cases.create(case_id, summary)
    runner = setup.runner
    model = config.model or getattr(runner.llm, "model_name", "?")
    tools = ", ".join(sorted(t.name for t in setup.tools))
    print(f"模型：{model}{'（脚本，dry-run）' if args.dry_run else ''}\n工具：{tools}\n")

    message = args.message or input("请描述你的问题：").strip()
    progress = Progress(runner, case_id)
    info = progress.run(lambda: runner.start_run(case_id, message))
    while info.status == "waiting_user":
        reply = ask_user(info.wait, args.auto_reply)
        if reply is None:
            info = runner.cancel_run(info.run_id)
            break
        info = progress.run(lambda: runner.resume_run(
            info.run_id, reply, wait_id=info.wait.wait_id, expected_case_version=info.case_version))
    report(runner, info)
    return 0 if info.status == "completed" else 1


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--message", help="first message; asked interactively when omitted")
    p.add_argument("--dry-run", action="store_true", help="scripted model, no API key or network for the model")
    p.add_argument("--model", help="override the model, e.g. a stronger tool-calling model")
    p.add_argument("--conversation-route", choices=["auto", "consultation", "case_work"], default="auto",
                   help="semantic routing by default, or explicit consultation/business entry")
    p.add_argument("--pilot-config", type=Path, help="use budgets from evaluation/configs/*.json")
    p.add_argument("--fact", action="append", default=[], metavar="KEY=VALUE", help="initial confirmed fact")
    p.add_argument("--dispute-type", help="e.g. traffic_accident")
    p.add_argument("--mock-statutes", action="store_true", help="use the mock search_statutes")
    p.add_argument("--no-mock-tools", action="store_true", help="do not register member 3's mock tools")
    p.add_argument("--auto-reply", help="answer every question with this text instead of asking")
    return p.parse_args()


def dry_run_llm() -> ScriptedLLM:
    return ScriptedLLM([
        tool_call("ask_user", questions=[{"question_id": "q1", "text": "事故发生在哪一天？对方是否投保了交强险？"}]),
        tool_call("search_statutes", query="机动车交通事故 交强险 赔偿", top_k=3),
        tool_call("finish", answer="（dry-run 脚本回答）可先向交强险保险人主张赔偿，不足部分再由商业险和侵权人承担。",
                  open_issues=["这是脚本模型的固定回答，未经真实模型分析"]),
    ])


class Progress:
    """Runs a blocking start/resume in a thread and prints each step once as it finishes."""

    def __init__(self, runner: AgentRunner, case_id: str) -> None:
        self.runner, self.case_id = runner, case_id
        self.run_id: str | None = None
        self.cursor = 0

    def run(self, call) -> RunInfo:
        result: dict = {}

        def work():
            try:
                result["info"] = call()
            except BaseException as e:  # re-raised in the main thread
                result["error"] = e

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        while True:
            alive = thread.is_alive()
            self._print_new_steps()
            if not alive:
                break
            time.sleep(0.3)
        if "error" in result:
            raise result["error"]
        return result["info"]

    def _print_new_steps(self) -> None:
        if self.run_id is None:
            self.run_id = next((cp.run_id for cp in self.runner.store.list_checkpoints()
                                if cp.case_id == self.case_id), None)
            if self.run_id is None:
                return
        while True:
            page = self.runner.export_trace(self.run_id, cursor=self.cursor)
            for step in page.steps:
                print_step(step)
            self.cursor += len(page.steps)
            if len(page.steps) < 100:
                break


def print_step(step: TraceStep) -> None:
    name = step.tool_name or ""
    seconds = f"{step.duration_ms / 1000:.1f}s"
    print(f"  · #{step.step_id:<2} {step.step_type:<11} {name:<16} {step.status:<12} {seconds:>6}  {step.result_summary[:70]}")


def ask_user(wait: Wait, auto_reply: str | None) -> UserReply | None:
    """None means the user cancelled."""
    print()
    if wait.kind == "route_selection":
        print(f"Agent 请你选择处理路线：\n{wait.comparison}")
        for i, option in enumerate(wait.options, 1):
            print(f"  {i}. {option.label}（{option.route}）")
        if auto_reply is not None:
            return UserReply(route_choice=wait.options[0].route)
        while True:
            choice = input("输入编号（q 取消）：").strip()
            if choice.lower() == "q":
                return None
            if choice.isdigit() and 1 <= int(choice) <= len(wait.options):
                return UserReply(route_choice=wait.options[int(choice) - 1].route)

    label = "Agent 请你补充材料" if wait.kind == "material_request" else "Agent 追问"
    answers = {}
    for q in wait.questions:
        print(f"{label}：{q.text}")
        if auto_reply is not None:
            print(f"  （自动回答）{auto_reply}")
            answers[q.question_id] = auto_reply
            continue
        answer = input("  你的回答（直接回车跳过，q 取消）：").strip()
        if answer.lower() == "q":
            return None
        if answer:
            answers[q.question_id] = answer
    return UserReply(answers=answers) if answers else UserReply(text="用户没有回答这些问题。")


def report(runner: AgentRunner, info: RunInfo) -> None:
    print(f"\n对话入口：{info.conversation_route}")
    print(f"\n状态：{info.status}")
    if info.error:
        print(f"错误：{info.error}")
    final = info.final
    if final:
        print(f"\n回答：\n{final.answer}")
        if final.citations:
            print("\n引用：")
            refs = {s.source_id: s for s in final.source_refs}
            for c in final.citations:
                ref = refs.get(c.source_id)
                print(f"  · {ref.title + str(ref.locator) if ref else c.source_id} ← {c.location}")
        elif final.source_refs:
            print("\n检索到的来源：" + "、".join(f"{s.title}{s.locator}" for s in final.source_refs))
        if final.open_issues:
            print("\n未解决事项：")
            for issue in final.open_issues:
                print(f"  · {issue}")
    u = info.usage
    cost = f"{u.estimated_cost} {u.cost_currency}" if u.estimated_cost is not None else "未知（未配置单价）"
    tokens = f"{u.prompt_tokens + u.completion_tokens}" + ("" if u.tokens_complete else "（部分调用未报告）")
    print(f"\n用量：{u.model_calls} 次模型调用，{u.tool_calls} 次工具调用，{tokens} tokens，"
          f"费用 {cost}，耗时 {u.duration_ms / 1000:.1f}s")
    print(f"记录：var/runs/{info.run_id}/")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已中断。")
        sys.exit(130)
