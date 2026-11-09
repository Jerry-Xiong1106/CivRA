"""Data boundaries and invalid experiment inputs; runs with unittest."""

import json
from pathlib import Path
import tempfile
import unittest

from pydantic import ValidationError

from evaluation.schemas.tasks import GoldTask, PublicTask, load_public_tasks, validate_bundle

ROOT = Path(__file__).resolve().parents[2]


class TaskContractTests(unittest.TestCase):
    def setUp(self):
        self.task = json.loads((ROOT / "evaluation/datasets/pilot/tasks.jsonl").read_text().splitlines()[0])
        self.hidden = dict(task_id=self.task["task_id"], dataset_version=self.task["dataset_version"], facts=[])
        self.gold = dict(
            task_id=self.task["task_id"], dataset_version=self.task["dataset_version"],
            review_status="draft", key_facts=["SCORER_ONLY_FACT"],
            acceptable_outputs=["SCORER_ONLY_ANSWER"], prohibited_inventions=["invented evidence"],
            scoring_items=[dict(item_id="balance", dimension="task_outcome", description="check balance",
                                zero="wrong", one="partial", two="correct", required_for_success=True,
                                applicable_modes=["direct_llm", "rag", "fixed_workflow", "agent"])],
        )

    def bundle(self, task=None, hidden=None, gold=None):
        with tempfile.TemporaryDirectory() as folder:
            paths = [Path(folder) / f"{name}.jsonl" for name in ("public", "hidden", "gold")]
            for path, data in zip(paths, (task or self.task, hidden or self.hidden, gold or self.gold)):
                records = data if isinstance(data, list) else [data]
                path.write_text(''.join(json.dumps(r) + '\n' for r in records))
            return validate_bundle(*paths)

    def test_public_examples_load(self):
        self.assertEqual(len(load_public_tasks(ROOT / "evaluation/datasets/pilot/tasks.jsonl")), 5)

    def test_model_input_has_only_public_allowlist(self):
        payload = PublicTask.model_validate(self.task).to_model_input()
        self.assertEqual(set(payload), {"initial_input", "user_goal", "selected_route", "materials", "output_requirements"})
        self.assertNotIn("source_case_id", payload)
        self.assertNotIn("tags", payload)

    def test_private_fields_are_rejected_in_public_file(self):
        for key in ("hidden_facts", "gold_annotations", "review_status", "api_key"):
            with self.subTest(key=key), self.assertRaises(ValidationError):
                PublicTask.model_validate(dict(self.task, **{key: "PRIVATE_SENTINEL"}))

    def test_pilot_cannot_be_final_test(self):
        with self.assertRaises(ValidationError):
            PublicTask.model_validate(dict(self.task, split="final_test"))

    def test_empty_input_is_rejected(self):
        with self.assertRaises(ValidationError):
            PublicTask.model_validate(dict(self.task, initial_input="   "))

    def test_duplicate_task_ids_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate task_id"):
            self.bundle(task=[self.task, self.task])

    def test_orphan_private_record_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "IDs must match"):
            self.bundle(hidden=dict(self.hidden, task_id="orphan"))

    def test_version_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "version mismatch"):
            self.bundle(gold=dict(self.gold, dataset_version="wrong"))

    def test_static_task_rejects_hidden_fact(self):
        fact = dict(fact_id="repayment", disclosure_condition="asked about repayment",
                    accepted_question_examples=["any repayment?"], response="CONTROLLER_ONLY_REPLY")
        with self.assertRaisesRegex(ValueError, "Static task"):
            self.bundle(hidden=dict(self.hidden, facts=[fact]))

    def test_final_test_requires_annotation_review(self):
        with self.assertRaisesRegex(ValueError, "need review"):
            self.bundle(task=dict(self.task, phase="formal", split="final_test"))

    def test_case_family_cannot_cross_splits(self):
        other = dict(self.task, task_id="formal-001", phase="formal", split="final_test")
        reviewed = dict(self.gold, task_id="formal-001", review_status="reviewed", reviewed_by=["reviewer"])
        with self.assertRaisesRegex(ValueError, "crosses data splits"):
            self.bundle(task=[self.task, other], hidden=[self.hidden, dict(self.hidden, task_id="formal-001")],
                        gold=[self.gold, reviewed])

    def test_reviewed_annotation_requires_reviewer(self):
        with self.assertRaises(ValidationError):
            GoldTask.model_validate(dict(self.gold, review_status="reviewed"))

    def test_duplicate_score_ids_are_rejected(self):
        with self.assertRaises(ValidationError):
            GoldTask.model_validate(dict(self.gold, scoring_items=self.gold["scoring_items"] * 2))

    def test_summary_does_not_export_private_values(self):
        result = self.bundle()
        self.assertNotIn("SCORER_ONLY", json.dumps(result))
        self.assertEqual(result["tasks"], 1)

    def test_duplicate_json_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tasks.jsonl"
            path.write_text('{"task_id":"one","task_id":"two"}\n')
            with self.assertRaisesRegex(ValueError, "Duplicate JSON key"):
                load_public_tasks(path)


if __name__ == "__main__":
    unittest.main()
