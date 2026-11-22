import copy
from pathlib import Path
import unittest

from evaluation.scoring.review import prepare_static_review, restore_review_ids, summarize_reviews
from evaluation.schemas.tasks import GoldTask, load_public_tasks


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.gold = GoldTask(task_id='one', dataset_version='v1', review_status='draft', key_facts=[],
                             acceptable_outputs=['conditional answer'], prohibited_inventions=['invented fact'],
                             scoring_items=[dict(item_id='check',dimension='evidence',description='gap',zero='missing',
                                                 one='partial',two='complete',applicable_modes=['rag'],required_for_success=True)])
        self.runs = [dict(task_id='one',experiment_run_id='run-one',mode='rag',task_type='static',status='completed')]
        self.reviews = [dict(experiment_run_id='run-one',item_id='check',score=2,reason='observed output',reviewer='assistant',review_kind='assistant_provisional')]

    def test_success_and_mean(self):
        summary = summarize_reviews(self.runs,[self.gold],self.reviews)[0]
        self.assertEqual(summary['task_successes'],1)
        self.assertEqual(summary['dimensions']['evidence'],2)

    def test_missing_review_fails(self):
        with self.assertRaises(ValueError): summarize_reviews(self.runs,[self.gold],[])

    def test_duplicate_review_fails(self):
        with self.assertRaises(ValueError): summarize_reviews(self.runs,[self.gold],self.reviews*2)

    def test_boolean_is_not_a_score(self):
        bad=copy.deepcopy(self.reviews);bad[0]['score']=True
        with self.assertRaises(ValueError): summarize_reviews(self.runs,[self.gold],bad)

    def test_truncated_answer_cannot_pass_required_items(self):
        self.runs[0]['delivery_status'] = 'truncated'
        summary = summarize_reviews(self.runs, [self.gold], self.reviews)[0]
        self.assertEqual((summary['completed'], summary['delivered'], summary['task_successes']), (1, 0, 0))

    def test_static_review_packet_excludes_mode_and_trace(self):
        root = Path(__file__).resolve().parents[2]
        static, interactive, *_ = load_public_tasks(root / 'evaluation/datasets/pilot/tasks.jsonl')
        gold = self.gold.model_copy(update={'task_id': static.task_id, 'dataset_version': static.dataset_version})
        common = gold.scoring_items[0].model_copy(update={
            'applicable_modes': ['direct_llm', 'rag', 'fixed_workflow', 'agent']
        })
        gold.scoring_items.append(common.model_copy(update={'item_id': 'common'}))
        run = dict(self.runs[0], task_id=static.task_id, output='结论', trace=['TRACE'])
        other = dict(run, task_id=interactive.task_id, experiment_run_id='run-interactive')
        other_gold = gold.model_copy(update={'task_id': interactive.task_id})
        packet, key = prepare_static_review([run, other], [static, interactive], [gold, other_gold])
        self.assertEqual(len(packet), 1)
        case = packet[0]
        self.assertEqual(set(case), {'review_id', 'input', 'answer', 'rubric', 'annotation_status'})
        self.assertEqual([item['item_id'] for item in case['rubric']], ['common'])
        restored = restore_review_ids([{'review_id': case['review_id'], 'item_id': 'common'}], key)
        self.assertEqual(restored, [{'experiment_run_id': 'run-one', 'item_id': 'common'}])

if __name__=='__main__': unittest.main()
