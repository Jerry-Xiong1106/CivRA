"""Semantic two-route entry, explicit override, enforcement and persistence."""
import json
import pytest

from backend.agent.factory import build_runner
from backend.agent.model.llm import ScriptedLLM, tool_call
from backend.agent.runner import AgentRunner
from backend.agent.runtime.store import RunStore
from backend.common.schemas import RunConfig, LLMResponse, UserReply


def decision(route, reason="用户本次目标"):
    return LLMResponse(content=json.dumps({"route": route, "reason": reason}, ensure_ascii=False))


def setup(tmp_path, script, **config):
    s=build_runner(llm=ScriptedLLM(script), real_statutes=False, dotenv=None,
                   store=RunStore(tmp_path), config=RunConfig(**config))
    s.cases.create("c", {})
    return s


def test_auto_classification_is_a_recorded_model_call_and_guards_writes(tmp_path):
    def classify(messages, tools):
        assert tools == []
        request=json.loads(messages[-1]["content"])
        assert request['user_request']=='事故赔偿是否按伤情轻重划分？只解释一般规则'
        return decision('consultation')
    def answer(messages, tools):
        assert {t.name for t in tools} == {'search_statutes','ask_user','request_route_choice','finish'}
        return tool_call('update_case', facts={'forged':True})
    s=setup(tmp_path,[classify,answer,tool_call('finish',answer='解释当前问题')])
    info=s.runner.start_run('c','事故赔偿是否按伤情轻重划分？只解释一般规则')
    assert info.status=='completed' and info.conversation_route=='consultation'
    assert info.usage.model_calls==3 and info.usage.prompt_tokens==300
    assert s.cases.get_snapshot('c').case_version==1
    trace=s.runner.get_trace(info.run_id)
    assert trace[0].arguments['purpose']=='conversation_routing'
    assert next(t for t in trace if t.tool_name=='update_case').error.code=='NOT_ALLOWED'


def test_case_work_allows_explicit_operations_but_still_checks_stage(tmp_path):
    s=setup(tmp_path,[decision('case_work'),tool_call('update_case',facts={'x':1}),
                      tool_call('draft_document',template_id='t'),tool_call('finish',answer='已记录，诉讼尚未选择')])
    info=s.runner.start_run('c','帮我记录事实并准备材料')
    assert info.conversation_route=='case_work' and info.status=='completed'
    assert s.cases.get_snapshot('c').case_version==2
    assert next(t for t in s.runner.get_trace(info.run_id) if t.tool_name=='draft_document').error.code=='NOT_ALLOWED'


@pytest.mark.parametrize('bad',[LLMResponse(content='not JSON'),decision('litigation'),
                               tool_call('update_case',facts={'x':1})])
def test_invalid_routing_does_not_execute_a_tool_or_enable_business(tmp_path,bad):
    s=setup(tmp_path,[bad,tool_call('finish',answer='先提供咨询')])
    info=s.runner.start_run('c','测试')
    assert info.status=='completed' and info.conversation_route=='consultation'
    assert s.runner.store.load_checkpoint(info.run_id).routing_source=='fallback'
    assert info.usage.tool_calls==0 and s.cases.get_snapshot('c').case_version==1


def test_explicit_entry_skips_classifier(tmp_path):
    s=setup(tmp_path,[tool_call('finish',answer='done')],conversation_route='case_work')
    info=s.runner.start_run('c','按钮发起操作')
    assert info.usage.model_calls==1 and info.conversation_route=='case_work'
    assert s.runner.store.load_checkpoint(info.run_id).routing_source=='config'


def test_reply_restores_route_without_reclassification_and_user_choice_enables_work(tmp_path):
    s=setup(tmp_path,[decision('consultation'),tool_call('ask_user',questions=[{'question_id':'q','text':'哪项信息？'}])])
    info=s.runner.start_run('c','请先解释')
    model=ScriptedLLM([tool_call('request_route_choice',comparison='请选',options=[{'route':'mediation','label':'调解'}]),
                      tool_call('update_case',facts={'next_step':'test'}),tool_call('finish',answer='已选择调解')])
    runner=AgentRunner(model,s.tools,s.cases,RunStore(tmp_path))
    info=runner.resume_run(info.run_id,UserReply(text='我想继续处理'),wait_id=info.wait.wait_id)
    assert info.conversation_route=='consultation' and info.wait.kind=='route_selection'
    info=runner.resume_run(info.run_id,UserReply(route_choice='mediation'),wait_id=info.wait.wait_id)
    assert info.status=='completed' and info.conversation_route=='case_work'
    assert len(model.calls)==3 and s.cases.get_snapshot('c').stage=='mediation'


def test_routing_consumes_budget_and_cannot_repeat_on_idempotent_start(tmp_path):
    s=setup(tmp_path,[decision('consultation')],max_model_calls=1)
    info=s.runner.start_run('c','question',request_id='same')
    assert info.status=='budget_exhausted' and info.usage.model_calls==1
    assert s.runner.start_run('c','question',request_id='same').run_id==info.run_id
    assert len(s.runner.llm.calls)==1


def test_selected_litigation_route_does_not_force_business_entry(tmp_path):
    s=setup(tmp_path,[decision('consultation'),tool_call('finish',answer='解释起诉状的用途')])
    s.cases.set_stage('c','litigation')
    info=s.runner.start_run('c','起诉状是什么？不是让你生成文书。')
    assert info.status=='completed' and info.conversation_route=='consultation'
    assert s.cases.get_snapshot('c').stage=='litigation'
    visible=set(s.runner.llm.calls[-1]['tools'])
    assert 'draft_document' not in visible and 'update_case' not in visible


def test_provider_failure_falls_back_without_inventing_usage(tmp_path):
    def unavailable(messages,tools):
        raise ConnectionError('private provider details')
    s=setup(tmp_path,[unavailable,tool_call('finish',answer='先提供一般说明')])
    info=s.runner.start_run('c','question')
    assert info.status=='completed' and info.conversation_route=='consultation'
    assert not info.usage.tokens_complete and info.usage.model_calls==2
    assert 'private provider details' not in s.runner.get_trace(info.run_id)[0].error.message


def test_personal_case_can_ask_critical_question_before_any_business_tool(tmp_path):
    question = "事故发生时你是步行、骑车还是驾车？"
    s = setup(tmp_path, [
        decision("case_work"),
        tool_call("ask_user", questions=[{"question_id": "travel_mode", "text": question}]),
        tool_call("finish", answer="已收到你补充的步行情况，下一步可结合事故经过处理。"),
    ])
    info = s.runner.start_run("c", "我被电动车撞了，他比我伤得重，怎么处理")
    assert info.conversation_route == "case_work"
    assert info.status == "waiting_user"
    assert info.wait.questions[0].text == question
    assert not any(step.step_type == "tool_call" for step in s.runner.get_trace(info.run_id))
    info = s.runner.resume_run(info.run_id, UserReply(text="我当时步行"),
                               wait_id=info.wait.wait_id)
    assert info.status == "completed"
    assert s.runner.store.load_checkpoint(info.run_id).clarification_rounds == 1
    assert len(s.runner.llm.calls) == 3  # Reply resumes without another classifier.


@pytest.mark.parametrize("route", ["negotiation", "mediation", "litigation"])
def test_route_choice_can_continue_preparation_without_generating_plan(tmp_path, route):
    s = setup(tmp_path, [
        decision("case_work"),
        tool_call("request_route_choice", comparison="请选择处理方向",
                  options=[{"route": route, "label": route}]),
        tool_call("ask_user", questions=[{"question_id": "next_input", "text": "当前准备所需的一项信息？"}]),
        tool_call("finish", answer="本次准备内容；后续等待实际反馈。"),
    ])
    info = s.runner.start_run("c", "朋友借钱不还，请帮我选择方式并继续准备")
    info = s.runner.resume_run(info.run_id, UserReply(route_choice=route), wait_id=info.wait.wait_id)
    assert info.status == "waiting_user" and info.wait.kind == "clarification"
    assert s.cases.get_snapshot("c").stage == route
    info = s.runner.resume_run(info.run_id, UserReply(text="补充信息"), wait_id=info.wait.wait_id)
    assert info.status == "completed"
    assert not any(step.tool_name == "get_route_workflow" for step in s.runner.get_trace(info.run_id))
