"""One conversation flow with shared limits and route-selection events."""
from backend.agent.factory import build_runner
from backend.agent.model.llm import ScriptedLLM, tool_call
from backend.agent.runner import AgentRunner
from backend.agent.runtime.store import RunStore
from backend.common.schemas import LLMResponse, RunConfig, UserReply


def setup(tmp_path, script, **limits):
    result = build_runner(llm=ScriptedLLM(script), real_statutes=False, dotenv=None,
                          config=RunConfig(**limits), store=RunStore(tmp_path))
    result.cases.create("c", {})
    return result


def ask(n):
    return tool_call("ask_user", questions=[{"question_id": str(n), "text": f"必要信息{n}？"}])


def test_no_wording_can_bypass_search_limits_in_the_same_batch(tmp_path):
    calls = [tool_call("search_statutes", query=f"q{i}").tool_calls[0] for i in range(5)]
    s = setup(tmp_path, [LLMResponse(tool_calls=calls), tool_call("finish", answer="已有结果及缺口")], max_search_calls=3)
    info = s.runner.start_run("c", "被电动车撞了，他伤得比我重，怎么处理")
    assert info.status == "completed" and info.usage.tool_attempts == 3
    assert s.runner.store.load_checkpoint(info.run_id).search_calls == 3


def test_clarification_limits_survive_restart_without_blocking_route_selection(tmp_path):
    s = setup(tmp_path, [tool_call("ask_user", questions=[{"question_id":str(i),"text":str(i)} for i in range(3)]), ask(1)],
              max_clarification_rounds=2)
    info = s.runner.start_run("c", "朋友欠我钱怎么办")
    assert len(info.wait.questions) == 1
    model = ScriptedLLM([ask(2), ask(3), tool_call("finish", answer="先给方向"), tool_call("finish", answer="已进入调解")])
    runner = AgentRunner(model, s.tools, s.cases, RunStore(tmp_path))
    info = runner.resume_run(info.run_id, UserReply(text="不知道"), wait_id=info.wait.wait_id)
    info = runner.resume_run(info.run_id, UserReply(text="先给方向"), wait_id=info.wait.wait_id)
    assert info.status == "completed" and info.final.answer == "先给方向"
    assert runner.store.load_checkpoint(info.run_id).clarification_rounds == 2
    info = runner.select_route("c", "mediation", history_run_id=info.run_id)
    assert info.status == "completed" and s.cases.get_snapshot("c").stage == "mediation"


def test_common_limits_are_configurable_and_plain_answer_does_not_force_menu(tmp_path):
    s = setup(tmp_path,[ask(1),ask(2),tool_call("finish",answer="先给建议，保留未知")],max_clarification_rounds=1)
    info=s.runner.start_run("c","我想了解处理方式")
    info=s.runner.resume_run(info.run_id,UserReply(text="不知道"),wait_id=info.wait.wait_id)
    assert info.status=="completed"
    assert s.runner.store.load_checkpoint(info.run_id).clarification_rounds==1


def test_global_tool_limit_still_checks_each_call(tmp_path):
    calls=[tool_call("search_statutes",query="q").tool_calls[0] for _ in range(4)]
    s=setup(tmp_path,[LLMResponse(tool_calls=calls)],max_tool_calls=2)
    info=s.runner.start_run("c","test")
    assert info.status=="budget_exhausted" and info.usage.tool_calls==2


