"""Keep the public handoff cases executable through the evaluation entry point."""

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/case"))
from check_handoff import run_handoff_check
from phase4_support import load_scenarios


class HandoffTests(unittest.TestCase):
    def test_every_tool_has_four_scenarios_and_matching_manual_expectations(self):
        scenarios = load_scenarios()
        self.assertEqual(len(scenarios), 40)
        for tool in {item["tool"] for item in scenarios}:
            with self.subTest(tool=tool):
                self.assertEqual({item["category"] for item in scenarios if item["tool"] == tool},
                                 {"normal", "invalid", "missing", "conflict"})
        with TemporaryDirectory() as directory:
            summary = run_handoff_check(directory)
            self.assertEqual(summary["passed"], 40, summary["failures"])
            records = json.loads((Path(directory) / "results.json").read_text(encoding="utf-8"))
            for record in records:
                self.assertNotIn("expected", record["invocation"])
                self.assertEqual(record["evaluation_response"]["request_id"], record["invocation"]["context"]["request_id"])

    def test_pilot_annotations_are_development_drafts_with_traceable_scenarios(self):
        root = Path(__file__).resolve().parents[2]
        payload = json.loads((root / "data/examples/member3/phase4/pilot_annotations.json").read_text(encoding="utf-8"))
        scenarios = {item["scenario_id"] for item in load_scenarios()}
        self.assertEqual(len(payload["annotations"]), 10)
        for task in payload["annotations"]:
            with self.subTest(task=task["task_id"]):
                self.assertIn(task["scenario_id"], scenarios)
                self.assertEqual(task["split"], "development")
                self.assertEqual(task["review_status"], "pending_member5_review")
                self.assertTrue(task["acceptable_outputs"])
                self.assertTrue(task["must_not_invent"])
