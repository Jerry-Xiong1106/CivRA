import copy
import json
from pathlib import Path
import tempfile
import unittest

from backend.agent.model.llm import ScriptedLLM, tool_call
from backend.agent.case_port import InMemoryCaseStore
from backend.agent.runner import AgentRunner
from backend.agent.runtime.store import RunStore
from backend.common.schemas import LLMResponse, TokenUsage, UserReply, RunConfig
from tests.native_tools import mock_tool_fixture
from evaluation.adapters.team import ModelAdapter, RetrievalAdapter, AgentAdapter
from evaluation.baselines.consultation import run_baseline
from evaluation.schemas.tasks import load_public_tasks

ROOT = Path(__file__).resolve().parents[2]


class EmptyService:
    def search(self, query, top_k):
        return {"status": "no_results", "results": [], "reason": "no_candidates"}


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.task = load_public_tasks(ROOT / 'evaluation/datasets/pilot/tasks.jsonl')[0]
        self.config = json.loads((ROOT / 'evaluation/configs/pilot_v1.json').read_text())

    def model(self, response=None):
        return ModelAdapter(ScriptedLLM([response or LLMResponse(content='Answer', finish_reason='stop')]))

    def test_direct_has_no_retrieval_and_no_annotation_inputs(self):
        model = self.model()
        result = run_baseline(self.task, 'direct_llm', model, self.config)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['retrieval_calls'], 0)
        packet = json.loads(model.client.calls[0]['messages'][1]['content'])
        self.assertEqual(set(packet), {'initial_input','user_goal','selected_route','materials','output_requirements'})
        self.assertEqual(model.client.calls[0]['tools'], [])

    def test_rag_empty_result_is_not_failure(self):
        result = run_baseline(self.task, 'rag', self.model(), self.config, RetrievalAdapter(EmptyService()))
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['retrieval']['matches'], [])
        self.assertEqual(result['retrieval_calls'], 1)

    def test_retrieval_adapter_uses_shared_structured_source_contract(self):
        from backend.knowledge.retrieval.results import to_result
        from tests.rag.test_langchain_retrieval import provision
        p = provision('第一条', 'body')
        class Service:
            knowledge_version = 'test-snapshot'
            source_metadata = {}
            def search(self, query, top_k):
                return {'status': 'ok', 'query': query, 'warnings': [],
                        'results': [to_result(p, 1, 0.8, ['vector'])]}
        data = RetrievalAdapter(Service()).search_statutes('q', 5)
        ref = data['matches'][0]['source_ref']
        self.assertEqual(ref['source_version'], '2020-05-28')
        self.assertEqual(ref['effective_from'], '2021-01-01')
        self.assertEqual(ref['locator']['article'], '第一条')
        self.assertEqual(data['knowledge_version'], 'test-snapshot')
        p['page_start'] = 0
        with self.assertRaises(ValueError):
            RetrievalAdapter(Service()).search_statutes('q', 5)

    def test_failure_does_not_call_model_after_retrieval_error(self):
        class Broken:
            def search_statutes(self, *args):
                raise TimeoutError('private provider text')
        model = self.model()
        result = run_baseline(self.task, 'rag', model, self.config, Broken())
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['model_calls'], 0)
        self.assertNotIn('private provider text', json.dumps(result))

    def test_token_mapping_preserves_unknown(self):
        model = self.model(LLMResponse(content='Answer', usage=TokenUsage(prompt_tokens=12)))
        response = model.generate([], self.config)
        self.assertEqual(response['usage']['input_tokens'], 12)
        self.assertIsNone(response['usage']['output_tokens'])
        self.assertIsNone(response['cost'])

    def test_length_is_not_completed(self):
        result = run_baseline(self.task, 'direct_llm', self.model(LLMResponse(content='part', finish_reason='length')), self.config)
        self.assertEqual(result['status'], 'truncated')

    def test_unexpected_tool_calls_fail(self):
        result = run_baseline(self.task, 'direct_llm', self.model(tool_call('finish', answer='hi')), self.config)
        self.assertEqual(result['status'], 'failed')

    def test_known_token_budget_breach_retains_output(self):
        config = copy.deepcopy(self.config)
        config['budgets']['max_total_tokens'] = 1
        result = run_baseline(self.task, 'direct_llm', self.model(), config)
        self.assertEqual(result['status'], 'budget_exhausted')
        self.assertEqual(result['output'], 'Answer')

    def test_agent_adapter_resume_and_trace(self):
        cases = InMemoryCaseStore()
        cases.create('adapter-case')
        script = [tool_call('ask_user', questions=[{'question_id':'q1','text':'Any repayment?'}]), tool_call('finish', answer='done')]
        with tempfile.TemporaryDirectory() as tmp:
            runner = AgentRunner(ScriptedLLM(script), mock_tool_fixture(cases), cases, RunStore(Path(tmp)), default_config=RunConfig())
            result = AgentAdapter(runner).run('adapter-case','question',RunConfig(),'adapter-exp',
                                             lambda wait: UserReply(answers={'q1':'yes'}))
            self.assertEqual(result['run']['status'],'completed')
            self.assertEqual(len(result['disclosures']),1)
            self.assertTrue(result['trace'])


if __name__ == '__main__':
    unittest.main()
