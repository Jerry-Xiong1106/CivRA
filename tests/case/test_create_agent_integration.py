"""Current create_agent, real local retrieval and persistent business-tool integration."""
from backend.case.intake.updates import record_user_statement
from backend.common.schemas import RunConfig
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backend.tools.policy import describe_tool
from backend.agent.factory import build_runner
from backend.agent.model.llm import ScriptedLLM, tool_call
from backend.agent.runner import VersionConflict
from backend.agent.runtime.store import RunStore
from backend.case import CaseService, SQLiteCaseRepository
from scripts.case.fixtures import load_development_case
from backend.tools.case_results import to_runtime_tool_result
from backend.case.research.knowledge_bridge import CorpusSourceResolver
from backend.case.models import BusinessResult, SourcePointer
from backend.agent.case_adapter import RepositoryCasePort
from backend.common.schemas import ToolResult, UserReply
from backend.knowledge.config import RetrievalConfig
from backend.knowledge.keyword.bm25_index import build_index
from backend.knowledge.retrieval.service import RetrievalService

ROOT = Path(__file__).resolve().parents[2]


class CreateAgentIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        paths = [ROOT/'data/processed'/name for name in
                 ('civil_code.jsonl', 'civil_procedure_law.jsonl', 'road_traffic_safety_law.jsonl')]
        cls.retrieval = RetrievalService(RetrievalConfig(channel='keyword'), keyword_index=build_index(paths))
        cls.resolver = CorpusSourceResolver.from_directory()

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.repository = SQLiteCaseRepository(self.directory/'cases.sqlite3')
        self.case = self.repository.create_case(load_development_case())
        self.service = CaseService(self.repository, source_resolver=self.resolver)

    def setup_runner(self, script):
        setup = build_runner(config=RunConfig(), llm=ScriptedLLM(script), case_service=self.service,
                             retrieval_service=self.retrieval, store=RunStore(self.directory/'runs'), dotenv=None)

        return setup

    def test_search_format_corrections_do_not_use_the_retrieval_quota(self):
        setup = self.setup_runner([
            tool_call("search_statutes", query="《民法典》第一千一百六十五条至第一千一百七十九条"),
            tool_call("search_statutes", query="《道路交通安全法》第七十五条至第七十六条"),
            tool_call("search_statutes", query="《民法典》第一千一百七十九条"),
            tool_call("search_statutes", query="《道路交通安全法》第七十六条"),
            tool_call("finish", answer="已核对本轮检索结果。"),
        ])
        info = setup.runner.start_run(self.case.case_id, "请检索本案所需条文。",
                                     config=RunConfig(max_search_calls=1, max_tool_failures=3))
        searches = [s for s in setup.runner.get_trace(info.run_id) if s.tool_name == "search_statutes"]
        assert info.status == "completed"
        assert [s.error.code if s.error else s.status for s in searches] == [
            "INVALID_ARGS", "INVALID_ARGS", "ok", "NOT_ALLOWED",
        ]
        assert setup.runner.store.load_checkpoint(info.run_id).search_calls == 1

    def test_agent_update_uses_saved_confirmation_timestamp_and_runtime_version(self):
        def save_facts(messages, tools):
            snapshot = self.repository.get_case(self.case.case_id)
            statement = next(s for s in snapshot.statements if s.text == "我确认双方同等责任，目前看到的医疗费票据是18000元。")
            facts = [
                {"fact_id": "f_test_liability", "field": "liability_finding",
                 "value_type": "text", "value": "同等责任",
                 "confirmation": {"message_id": statement.message_id}},
                {"fact_id": "f_test_medical", "field": "medical_expense_reported",
                 "value_type": "money", "value": {"amount": "18000", "currency": "CNY"},
                 "confirmation": {"message_id": statement.message_id}},
            ]
            for fact in facts:
                fact.update(provenance="user_statement", confirmation_status="confirmed", statement_refs=[statement.message_id])
            return tool_call("update_case", changes={"upsert_facts": facts}, change_reason="记录明确确认的事实")

        setup = self.setup_runner([save_facts, tool_call("finish", answer="可以继续准备调解方案。")])
        info = setup.runner.start_run(self.case.case_id, "我确认双方同等责任，目前看到的医疗费票据是18000元。")
        assert info.status == "completed"
        snapshot = self.repository.get_case(self.case.case_id)
        by_id = {item.fact_id: item for item in snapshot.facts}
        for key in ("f_test_liability", "f_test_medical"):
            confirmation = by_id[key].confirmation
            statement = next(s for s in snapshot.statements if s.message_id == confirmation.message_id)
            assert (confirmation.confirmed_by, confirmation.confirmed_at) == (statement.actor_id, statement.created_at)

    def test_invalid_case_updates_leave_budget_for_the_requested_answer(self):
        invalid = tool_call("update_case", change_reason="登记信息", changes={"upsert_facts": [{
            "fact_id": "f_test_bad", "field": "injury", "value_type": "text", "value": "手腕骨折",
            "provenance": "user_statement", "confirmation_status": "unconfirmed", "statement_refs": ["missing"],
        }]})

        def after_third_failure(messages, tools):
            assert "update_case" not in {tool.name for tool in tools}
            return invalid

        setup = self.setup_runner([
            invalid, invalid, invalid, after_third_failure,
            tool_call("finish", answer="基于目前陈述先准备调解方案。"),
        ])
        info = setup.runner.start_run(self.case.case_id, "我要准备调解方案。")
        assert info.status == "completed"
        steps = [step for step in setup.runner.get_trace(info.run_id) if step.tool_name == "update_case"]
        assert [step.error.code for step in steps] == ["INVALID_ARGS", "INVALID_ARGS", "INVALID_ARGS", "NOT_ALLOWED"]
        assert steps[2].error.details["next_action"]
        assert steps[0].error.details["issues"][0]["message"]
        assert info.final.answer == "基于目前陈述先准备调解方案。"

    def test_factory_runs_real_business_and_local_rag_and_preserves_sources(self):
        setup = self.setup_runner([
            tool_call('check_evidence'),
            tool_call('search_statutes', query='民法典第六百八十条'),
            tool_call('finish', answer='已读取候选条文。', citations=[{
                'source_id':'中华人民共和国民法典::2020-05-28::第六百八十条',
                'location':'已读取候选条文。'}]),
        ])
        self.assertIsInstance(setup.cases, RepositoryCasePort)
        self.assertEqual(len(setup.tools), 12)
        self.assertEqual(sum(t.name == "find_form_template" for t in setup.tools), 1)
        info = setup.runner.start_run(self.case.case_id, '请核对证据并检索民法典第六百八十条。')
        self.assertEqual(info.status, 'completed')
        trace = setup.runner.get_trace(info.run_id)
        tools = [step for step in trace if step.step_type == 'tool_call']
        self.assertEqual([(step.tool_name, step.status) for step in tools],
                         [('check_evidence', 'insufficient'), ('search_statutes', 'ok')])
        self.assertTrue(any(ref.source_type == 'user_material' for ref in tools[0].source_refs))
        ref = next(ref for ref in info.final.source_refs if ref.source_type == 'statute')
        self.assertEqual(ref.source_version, '2020-05-28')
        self.assertEqual(ref.effective_from, '2021-01-01')
        self.assertEqual(ref.locator.page_start, 97)
        original = self.resolver(SourcePointer(source_id=ref.source_id, source_version=ref.source_version))
        result = setup.runner.get_step_result(info.run_id, tools[1].result_ref)['result']
        self.assertEqual(result['data']['matches'][0]['text'], original['text'])
        self.assertNotIn('source_refs', result)

    def test_actual_rag_source_can_be_reviewed_by_business_verifier(self):
        response = self.retrieval.search('民法典第六百八十条')
        from backend.tools.knowledge_tools import to_tool_result
        match = to_tool_result(response).data['matches'][0]
        ref = match['source_ref']
        pointer = {'source_id':ref['source_id'], 'source_version':ref['source_version']}
        answer = '需要核对的合成主张。'
        result = self.service.review_answer({
            'context':{'case_id':self.case.case_id,'request_id':'review-current-rag',
                       'experiment_run_id':None,'deadline_at':None},
            'case_version':1,'answer':answer,'retrieved_sources':[pointer],
            'citations':[{'start':0,'end':len(answer),'source_ref':pointer,'source_quote':match['text']}],
        })
        self.assertEqual(result.data.citation_audit['valid_citation_count'], 1)
        self.assertIn('semantic_citation_support', result.data.checks_not_performed)

    def test_mock_statute_mode_keeps_the_ten_real_business_tools(self):
        setup = build_runner(config=RunConfig(), llm=ScriptedLLM([tool_call('check_evidence'), tool_call('finish', answer='done')]),
                             case_service=self.service, real_statutes=False,
                             store=RunStore(self.directory/'runs'), dotenv=None)

        self.assertEqual(len(setup.tools), 12)
        self.assertEqual(sum(t.name == "find_form_template" for t in setup.tools), 1)
        self.assertEqual(describe_tool(next(t for t in setup.tools if t.name == 'update_case')).owner, 'member3')
        self.assertIn('changes', describe_tool(next(t for t in setup.tools if t.name == 'update_case')).parameters['properties'])
        self.assertEqual(setup.runner.start_run(self.case.case_id, '检查合成案件').status, 'completed')

    def test_start_resume_restart_and_retries_record_user_messages_once(self):
        setup = self.setup_runner([tool_call('ask_user', questions=[{'question_id':'q','text':'是否有其他材料？'}])])
        info = setup.runner.start_run(self.case.case_id, '开始咨询', request_id='start-once')
        self.assertEqual(info.status, 'waiting_user')
        self.assertEqual(info.case_version, 2)
        again = setup.runner.start_run(self.case.case_id, '开始咨询', request_id='start-once')
        self.assertEqual(again.run_id, info.run_id)
        resumed = self.setup_runner([tool_call('check_evidence'), tool_call('finish', answer='done')])
        completed = resumed.runner.resume_run(info.run_id, UserReply(text='还有一份票据'),
            wait_id=info.wait.wait_id, expected_case_version=2, request_id='resume-once')
        self.assertEqual(completed.status, 'completed')
        self.assertEqual(completed.case_version, 3)
        repeated = resumed.runner.resume_run(info.run_id, UserReply(text='还有一份票据'),
            wait_id=info.wait.wait_id, expected_case_version=2, request_id='resume-once')
        self.assertEqual(repeated, completed)
        statements = self.repository.get_case(self.case.case_id).statements
        self.assertEqual([s.text for s in statements][-2:], ['开始咨询', '还有一份票据'])

    def test_stale_resume_cannot_persist_a_user_message(self):
        setup = self.setup_runner([tool_call('ask_user', questions=[{'question_id':'q','text':'补充？'}])])
        info = setup.runner.start_run(self.case.case_id, '咨询')
        with self.assertRaises(VersionConflict):
            setup.runner.resume_run(info.run_id, UserReply(text='过期请求'),
                                    wait_id=info.wait.wait_id, expected_case_version=1)
        self.assertEqual(self.repository.get_case(self.case.case_id).case_version, 2)

    def test_application_recorded_message_is_not_written_twice(self):
        record_user_statement(self.repository, case_id=self.case.case_id, expected_case_version=1, request_id='app-saved',
            message_id='app-message', actor_id='authenticated-user', text='已保存输入', created_at=datetime.now(timezone.utc))
        setup = self.setup_runner([tool_call('finish', answer='done')])
        info = setup.runner.start_run(self.case.case_id, '已保存输入', message_id='app-message')
        self.assertEqual(info.case_version, 2)
        self.assertEqual(len(self.repository.get_case(self.case.case_id).statements), 2)

    def test_retry_after_checkpoint_failure_reuses_saved_message(self):
        setup = self.setup_runner([tool_call('finish', answer='done')])
        with patch.object(setup.runner.store, 'save_checkpoint', side_effect=OSError('synthetic disk failure')):
            with self.assertRaises(OSError):
                setup.runner.start_run(self.case.case_id, '同一条输入', request_id='retry-initial-save')
        info = setup.runner.start_run(self.case.case_id, '同一条输入', request_id='retry-initial-save')
        self.assertEqual(info.status, 'completed')
        self.assertEqual(info.case_version, 2)
        self.assertEqual(len(self.repository.get_case(self.case.case_id).statements), 2)

    def test_legacy_pointer_emits_canonical_runtime_version(self):
        pointer = SourcePointer(source_id='中华人民共和国民法典::2020-05-28::第六百八十条', source_version='2021-01-01')
        source = self.resolver(pointer)
        result = BusinessResult(case_id=self.case.case_id, case_version=1, status='ok',
                                summary='synthetic', data={}, source_refs=[pointer])
        converted = to_runtime_tool_result(result, source_resolver=self.resolver, result_type=ToolResult)
        ref = converted.data['matches'][0]['source_ref']
        self.assertEqual(source['version'], '2021-01-01')
        self.assertEqual(ref['source_version'], '2020-05-28')
        self.assertEqual(converted.data['matches'][0]['text'], source['text'])
        self.assertEqual(converted.data['source_refs'], [pointer.model_dump(mode='json')])

    def test_ambiguous_legacy_version_is_rejected(self):
        raw = deepcopy(self.retrieval.provisions[0])
        other = deepcopy(raw)
        other['publication_date'] = '2022-01-01'
        resolver = CorpusSourceResolver([raw, other])
        with self.assertRaises(KeyError):
            resolver(SourcePointer(source_id=raw['provision_id'], source_version=raw['effective_date']))
        self.assertEqual(resolver(SourcePointer(source_id=raw['provision_id'], source_version='2022-01-01'))['text'], other['text'])

    def test_missing_publication_uses_the_same_content_hash_as_rag(self):
        raw = deepcopy(self.retrieval.provisions[0])
        raw['publication_date'] = None
        resolver = CorpusSourceResolver([raw])
        version = 'sha256:' + hashlib.sha256(raw['text'].encode('utf-8')).hexdigest()
        source = resolver(SourcePointer(source_id=raw['provision_id'], source_version=version))
        self.assertEqual(source['source_version'], version)


if __name__ == '__main__':
    unittest.main()
