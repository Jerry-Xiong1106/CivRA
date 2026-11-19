"""Run phase-one contract validation, not evidence analysis or model inference."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.case import validate_case_snapshot
from backend.tools.case_manifest import get_contract_manifest
from scripts.case.fixtures import load_development_case
from backend.agent.case_adapter import runtime_case_view
from evaluation.adapters.case_results import to_evaluation_result
from backend.tools.case_results import to_runtime_tool_result
from backend.case.contracts import InvocationContext  # noqa: E402


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", action="store_true", help="Print generated contract manifest and JSON schemas")
    args = parser.parse_args()
    if args.schema:
        print(json.dumps(get_contract_manifest(), ensure_ascii=False, indent=2))
        return
    snapshot = load_development_case()
    result = validate_case_snapshot(snapshot.model_dump(mode="json"))
    context = InvocationContext(request_id="demo-001", case_id=snapshot.case_id,
                                experiment_run_id="development-only", deadline_at=None)
    print(json.dumps({
        "case_id": snapshot.case_id,
        "case_version": snapshot.case_version,
        "synthetic": snapshot.synthetic,
        "facts": len(snapshot.facts), "evidence": len(snapshot.evidence),
        "validation": result.summary,
        "runtime_tool_status": to_runtime_tool_result(result)["status"],
        "runtime_case_fields": list(runtime_case_view(snapshot)),
        "evaluation_status": to_evaluation_result(result, context, duration_ms=0)["status"],
        "business_tools": get_contract_manifest()["business_operations"],
        "note": "本脚本只演示结构校验。真实证据检查、更新和目录生成请运行 demo_business.py；法律结论尚未实现。",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
