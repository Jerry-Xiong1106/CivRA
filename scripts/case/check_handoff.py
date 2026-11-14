"""Execute synthetic normal, invalid, missing and conflict handoff scenarios."""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.case import get_contract_manifest
from backend.case.contracts import RESULT_MODELS
from jsonschema import Draft202012Validator, ValidationError as SchemaValidationError
from phase4_support import assert_expected, load_scenarios, prepare_scenario


def run_handoff_check(directory):
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("choose a fresh output directory")
    records = []
    failures = []
    for scenario in load_scenarios():
        service, client, invocation = prepare_scenario(scenario, directory / scenario["scenario_id"])
        response = client.evaluate(scenario["tool"], invocation)
        failure = None
        try:
            assert_expected(response, scenario["expected"])
            if response["status"] == "ok":
                schema = RESULT_MODELS[scenario["tool"]].model_json_schema()
                Draft202012Validator(schema).validate(response["data"])
        except (AssertionError, KeyError, ValueError, SchemaValidationError) as exc:
            failure = str(exc)
            failures.append({"scenario_id": scenario["scenario_id"], "reason": failure})
        records.append({
            "scenario_id": scenario["scenario_id"], "tool": scenario["tool"], "category": scenario["category"],
            "invocation": invocation.model_dump(mode="json"), "evaluation_response": response,
            "expected": scenario["expected"], "passed": failure is None,
        })
    summary = {
        "scope": "synthetic development contract checks", "total": len(records),
        "passed": len(records) - len(failures), "failures": failures,
        "categories": dict(Counter(record["category"] for record in records)),
        "tools": sorted({record["tool"] for record in records}),
    }
    (directory / "results.json").write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (directory / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (directory / "contract-manifest.json").write_text(json.dumps(get_contract_manifest(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.output_dir:
        summary = run_handoff_check(args.output_dir)
    else:
        with TemporaryDirectory() as directory:
            summary = run_handoff_check(directory)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    raise SystemExit(bool(summary["failures"]))
