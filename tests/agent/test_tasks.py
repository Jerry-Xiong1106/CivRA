"""Task acceptance through real LangGraph tool cycles and SQLite checkpoints."""
import json

import pytest
from backend.agent.model.llm import tool_call
from backend.common.schemas import LLMResponse, UserReply
from tests.agent.test_routing import setup


def route(action, kind, goal="", **fields):
    return LLMResponse(content=json.dumps({
        "route": "case_work", "reason": "test",
        "task": {"action": action, "kind": kind, "deliverable": goal, **fields},
    }, ensure_ascii=False))


def finish(answer, **report):
    return tool_call("finish", answer=answer, task_result=report)


def test_pause_write_and_resume_item_review(tmp_path):
    s = setup(tmp_path, [
        route("new", "item_review", "核对对方五万元索赔", missing=["分项明细"]),
        finish("只有总额，请对方提供分项。", missing=["分项明细"]),
        route("new", "text", "起草索要明细的微信"),
        finish("请把金额和对应票据发我核对，谢谢。", delivered_text="请把金额和对应票据发我核对，谢谢。", missing=[]),
    ])
    first = s.runner.start_run("c", "核对五万元索赔")
    assert first.status == "completed" and first.wait is None and first.tasks[0].status == "partial"
    original_id = first.tasks[0].task_id
    second = s.runner.start_run("c", "先帮我写微信索要明细", history_run_id=first.run_id)
    assert [t.status for t in second.tasks] == ["paused", "delivered"]
    from backend.agent.runner import AgentRunner
    from backend.agent.runtime.store import RunStore
    from backend.agent.model.llm import ScriptedLLM
    model = ScriptedLLM([
        route("continue", "item_review", task_id=original_id, review_items=["医疗费300元", "交通费20元"]),
        finish("医疗费300元：有票据；交通费20元：尚缺票据。",
               items=[{"quote": "医疗费300元", "finding": "有票据"},
                      {"quote": "交通费20元", "finding": "尚缺票据"}], missing=["交通费票据"]),
    ])
    runner = AgentRunner(model, s.tools, s.cases, RunStore(tmp_path))
    third = runner.start_run("c", "医疗费300元，交通费20元", history_run_id=second.run_id, request_id="review")
    assert len(third.tasks) == 2 and third.tasks[0].task_id == original_id
    assert third.tasks[0].status == "partial" and third.tasks[0].missing == ["交通费票据"]
    assert runner.start_run("c", "医疗费300元，交通费20元", request_id="review").tasks == third.tasks
    assert len(model.calls) == 2
    assert runner.get_run(first.run_id).tasks == first.tasks
    assert runner.get_run(second.run_id).tasks == second.tasks
    assert len([m for m in runner.store.read_messages(first.run_id) if m.type == "human"]) == 1
    assert len([m for m in runner.store.read_messages(third.run_id) if m.type == "human"]) == 3
    assert not list(tmp_path.glob("*/checkpoint.json"))


@pytest.mark.parametrize("kind", ["document", "unsupported_document"])
def test_plan_or_invented_file_never_completes_export(tmp_path, kind):
    s = setup(tmp_path, [route("new", kind, "导出所需文书", formats=["docx"]),
                         tool_call("finish", answer="已准备好", output_refs=["fake"])])
    info = s.runner.start_run("c", "请生成可下载的文书")
    assert info.status == "completed"
    assert info.tasks[0].status != "delivered"
    assert not info.tasks[0].output_refs and not info.final.output_refs


def test_no_tool_answer_and_route_selection_preserve_goal(tmp_path):
    s = setup(tmp_path, [
        route("new", "document", "生成文书", formats=["pdf"]),
        tool_call("request_route_choice", comparison="选路线", options=[{"route":"litigation","label":"诉讼"}]),
        LLMResponse(content="这里是准备步骤"),
    ])
    info = s.runner.start_run("c", "我要文书")
    task_id = info.tasks[0].task_id
    info = s.runner.resume_run(info.run_id, UserReply(route_choice="litigation"), wait_id=info.wait.wait_id)
    assert info.tasks[0].task_id == task_id
    assert info.tasks[0].status != "delivered" and info.final.open_issues


def test_clarification_reply_can_temporarily_switch_tasks(tmp_path):
    s = setup(tmp_path, [
        route("new", "item_review", "核对金额"),
        tool_call("ask_user", questions=[{"question_id":"items", "text":"有分项吗？"}]),
        tool_call("finish", answer="请把明细发给我。",
                  task_change={"action":"new", "kind":"text", "deliverable":"拟微信"},
                  task_result={"delivered_text":"请把明细发给我。", "missing":[]}),
    ])
    info = s.runner.start_run("c", "帮我核对金额")
    info = s.runner.resume_run(info.run_id, UserReply(text="先帮我写微信"),
                               wait_id=info.wait.wait_id)
    assert [t.status for t in info.tasks] == ["paused", "delivered"]
    assert info.tasks[0].missing == ["有分项吗？"]
    assert len(s.runner.llm.calls) == 3


def test_total_alone_cannot_complete_item_review(tmp_path):
    s = setup(tmp_path, [route("new", "item_review", "逐项核对五万元"),
                         finish("五万元尚未核实", items=[{"quote":"五万元", "finding":"尚未核实"}], missing=[])])
    info = s.runner.start_run("c", "逐项核对五万元")
    assert info.wait is None and info.tasks[0].status == "partial"


def test_failed_calculation_preserves_grounded_inputs_and_specific_gap(tmp_path):
    s = setup(tmp_path, [
        route("new", "calculation", "计算设备租赁余款"),
        finish("本次需要支付99999元", answer_is_deliverable=True,
               known_inputs=["200元", "未说过的日期"], missing=["折让金额未知"]),
    ])
    info = s.runner.start_run("c", "租金200元，我不清楚折让金额。")
    assert info.final.answer.startswith("本轮无法确认")
    assert "租金200元，我不清楚折让金额。" in info.final.answer and "折让金额未知" in info.final.answer
    assert "99999" not in info.final.answer and "未说过的日期" not in info.final.answer
    assert info.wait is None and info.tasks[0].status == "partial" and not info.tasks[0].delivery_run_id


def test_next_question_waits_for_a_real_reply_before_text_delivery(tmp_path):
    s = setup(tmp_path, [
        route("new", "text", "准备交接短信"),
        finish("先核对交接地点", missing=["地点"], next_question="在哪里交接？"),
        finish("请在西门交接。", missing=[], answer_is_deliverable=True),
    ])
    info = s.runner.start_run("c", "请拟交接短信")
    assert info.status == "waiting_user" and info.tasks[0].status == "waiting_input"
    info = s.runner.resume_run(info.run_id, UserReply(text="西门"), wait_id=info.wait.wait_id)
    assert info.final.answer == "请在西门交接。" and info.tasks[0].status == "delivered"


def test_exhausted_question_budget_keeps_blocker_without_fake_wait(tmp_path):
    s = setup(tmp_path, [route("new", "text", "准备交接短信"),
                         finish("先约地点。", next_question="在哪里交接？", missing=["地点"])],
              max_clarification_rounds=0)
    info = s.runner.start_run("c", "请拟交接短信")
    assert info.wait is None and info.tasks[0].status == "partial"
    assert info.final.open_issues == ["地点"]
    assert info.tasks[0].missing == ["地点"]


def test_finish_batches_user_gaps_in_last_round_and_keeps_other_work_separate(tmp_path):
    gaps = [
        {"label":"交接地点", "kind":"user_input", "question":"在哪里交接？"},
        {"label":"交接时间", "kind":"user_input", "question":"什么时候方便？"},
        {"label":"授权书", "kind":"material", "question":"能把授权书发给我吗？"},
        {"label":"清单校验", "kind":"verification"},
        {"label":"第三方回执", "kind":"external"},
    ]
    after = [dict(gaps[1], question="能确认交接时间吗？"), *gaps[2:]]
    s = setup(tmp_path, [
        route("new", "text", "安排交接"),
        finish("准备交接", gaps=gaps),
        finish("在东门交接，时间待定。", answer_is_deliverable=True, gaps=after),
    ], max_clarification_rounds=1)
    info = s.runner.start_run("c", "请安排交接")
    assert info.status == "waiting_user"
    first, second = info.wait.questions
    assert [first.gap, second.gap] == ["交接地点", "交接时间"]
    assert first.question_id != second.question_id
    task, = info.tasks
    assert task.missing == [g["label"] for g in gaps]
    assert task.missing_kinds == {g["label"]:g["kind"] for g in gaps}
    info = s.runner.resume_run(info.run_id, UserReply(answers={first.question_id: "东门",
                               second.question_id:"还不清楚"}), wait_id=info.wait.wait_id)
    assert info.wait is None and info.tasks[0].status == "partial"
    assert info.tasks[0].missing == [g["label"] for g in after]
    assert info.final.open_issues == info.tasks[0].missing
    assert s.runner.store.load_checkpoint(info.run_id).clarification_rounds == 1


def test_finish_does_not_rephrase_an_asked_gap_or_ask_for_external_work(tmp_path):
    gaps = [{"kind":"user_input", "label":"可联系时间", "question":"改个问法，几点合适？"},
            {"kind":"external", "label":"机构答复", "question":"机构答复了吗？"}]
    s = setup(tmp_path, [
        route("new", "general", "沟通准备"),
        tool_call("ask_user", questions=[{"question_id":"time", "gap":"可联系时间", "text":"什么时候方便联系？"}]),
        finish("先提供有条件的安排。", gaps=gaps),
    ])
    info = s.runner.start_run("c", "帮我准备沟通")
    info = s.runner.resume_run(info.run_id, UserReply(answers={"time":"暂时不知道"}), wait_id=info.wait.wait_id)
    assert info.wait is None and info.tasks[0].missing == ["可联系时间", "机构答复"]
    assert s.runner.store.load_checkpoint(info.run_id).clarification_rounds == 1


def test_mediation_plan_asks_about_payments_before_finishing(tmp_path):
    s = setup(tmp_path, [
        route("new", "mediation_plan", "准备能拿去谈的调解方案"),
        tool_call("ask_user", questions=[{"question_id": "injury", "text": "伤情如何？"}]),
        finish("先给你一个方案，建议一次性付款。", missing=["对方尚未提供费用凭证"],
               mediation_inputs={"payment_history": "伤情如何？"}),
        finish("把已垫付的2000元计入总额，余款分两次付；费用凭证到账后再定具体金额。",
               missing=["对方尚未提供费用凭证"],
               delivered_text="把已垫付的2000元计入总额，余款分两次付；费用凭证到账后再定具体金额。"),
    ])
    info = s.runner.start_run("c", "我骑电动车和另一辆电动车撞了，想通过调解解决。")
    goal_id = info.tasks[0].task_id
    info = s.runner.resume_run(info.run_id, UserReply(answers={"injury": "手腕骨折，已经出院。"}),
                               wait_id=info.wait.wait_id)
    assert info.status == "waiting_user" and info.final is None
    assert {q.field for q in info.wait.questions} == {"payment_history", "payment_preference"}
    assert info.tasks[0].task_id == goal_id
    answers = {"payment_history": "已垫付2000元", "payment_preference": "余款分两次付"}
    info = s.runner.resume_run(info.run_id, UserReply(answers={
        q.question_id: answers[q.field] for q in info.wait.questions
    }), wait_id=info.wait.wait_id)
    assert info.status == "completed"
    assert info.tasks[0].task_id == goal_id
    assert info.tasks[0].mediation_inputs == answers
    assert info.tasks[0].verified_missing == []
    assert info.tasks[0].status == "partial"
    assert info.tasks[0].missing == ["对方尚未提供费用凭证"]


def test_plain_mediation_answer_cannot_skip_unasked_payment_questions(tmp_path):
    s = setup(tmp_path, [
        route("new", "mediation_plan", "制定调解方案"),
        LLMResponse(content="未向用户交付的内部草稿"),
        LLMResponse(content="先给方案草稿，付款信息待确认。"),
    ])
    info = s.runner.start_run("c", "帮我拟个赔付调解方案")
    assert info.status == "waiting_user"
    assert not any("未向用户交付的内部草稿" in msg.text
                   for msg in s.runner.store.read_messages(info.run_id))
    reply = UserReply(answers={info.wait.questions[0].question_id: "不知道。"})
    info = s.runner.resume_run(info.run_id, reply, wait_id=info.wait.wait_id)
    assert info.status == "completed" and info.tasks[0].status == "partial"
    assert len(info.tasks[0].verified_missing) == 2


@pytest.mark.parametrize("preliminary", [False, True])
def test_mediation_can_finish_without_asking_again(tmp_path, preliminary):
    script = [
        route("new", "mediation_plan", "给调解方案", mediation_preliminary=preliminary),
        finish("调解方案正文", delivered_text="调解方案正文", missing=[], mediation_inputs={
            "payment_history": "已垫付2000元", "payment_preference": "余款分两次付",
        }),
    ]
    s = setup(tmp_path, script)
    user = ("先按现有信息给调解草案，不用追问" if preliminary else
            "我要调解方案。已垫付2000元，余款分两次付")
    info = s.runner.start_run("c", user)
    assert info.status == "completed" and info.tasks[0].status == "delivered"
    assert s.runner.store.load_checkpoint(info.run_id).clarification_rounds == 0
    assert bool(info.tasks[0].mediation_inputs) is not preliminary
