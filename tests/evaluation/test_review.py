import copy
import unittest

from evaluation.scoring.review import summarize_reviews
from evaluation.schemas.tasks import GoldTask


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


if __name__=='__main__': unittest.main()
