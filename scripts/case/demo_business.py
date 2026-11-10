"""Offline real business demo: inspect -> record user reply -> update -> inspect -> directory."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.case import CaseService, SQLiteCaseRepository, load_development_case  # noqa: E402
from backend.case.contracts import (  # noqa: E402
    BuildEvidenceDirectoryRequest, CheckEvidenceRequest, InvocationContext, UpdateCaseRequest,
)
from backend.case.models import CaseSnapshot  # noqa: E402
from backend.case.runtime import RepositoryCasePort  # noqa: E402
from demo_support import DEMO_REPLY, confirmed_demo_changes  # noqa: E402


def run_demo(database):
    repository = SQLiteCaseRepository(database)
    snapshot = repository.create_case(load_development_case())
    service = CaseService(repository)

    def context(request_id):
        return InvocationContext(request_id=request_id, case_id=snapshot.case_id,
                                 experiment_run_id="development-only", deadline_at=None)

    before = service.check_evidence(CheckEvidenceRequest(context=context("check-before"),
        case_version=snapshot.case_version, case_snapshot=snapshot))
    port = RepositoryCasePort(repository)
    recorded = port.record_user_reply(case_id=snapshot.case_id, expected_case_version=1,
        request_id="record-demo-reply", message_id="msg_demo_reply", actor_id="synthetic_user", text=DEMO_REPLY,
        created_at=datetime.now(timezone.utc))
    if recorded.status != "ok":
        raise RuntimeError(recorded.summary)
    snapshot = repository.get_case(snapshot.case_id)
    request = UpdateCaseRequest(context=context("apply-demo-confirmation"), expected_case_version=snapshot.case_version,
        changes=confirmed_demo_changes(snapshot), change_reason="依据合成用户明确回复确认事实、解决冲突并排除重复上传")
    updated = service.update_case(request)
    if updated.status != "ok":
        raise RuntimeError(updated.summary)
    replay = service.update_case(request)
    snapshot = repository.get_case(snapshot.case_id)
    after = service.check_evidence(CheckEvidenceRequest(context=context("check-after"),
        case_version=snapshot.case_version, case_snapshot=snapshot))
    port.set_stage(snapshot.case_id, "litigation")  # Explicit demo user route, not model-selected.
    snapshot = repository.get_case(snapshot.case_id)
    directory = service.build_evidence_directory(BuildEvidenceDirectoryRequest(context=context("directory"),
        case_version=snapshot.case_version, case_snapshot=snapshot))
    return {
        "synthetic": True, "before": before.model_dump(mode="json"),
        "after": after.model_dump(mode="json"), "directory": directory.model_dump(mode="json"),
        "final_case_version": snapshot.case_version,
        "idempotent_retry_kept_version": replay.case_version == updated.case_version,
        "history_versions": [item.case_version for item in repository.get_history(snapshot.case_id)],
        "note": "真实规则工具及 SQLite 更新；用户回复固定合成，未调用真实模型或作法律证明力判断。",
    }


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, help="A NEW SQLite path; existing demo case will never be overwritten")
    parser.add_argument("--output", type=Path, help="Optional result JSON path, normally under ignored var/")
    args = parser.parse_args()
    if args.db:
        result = run_demo(args.db)
    else:
        with TemporaryDirectory() as directory:
            result = run_demo(Path(directory) / "cases.sqlite3")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"before": result["before"]["summary"], "after": result["after"]["summary"],
        "directory": result["directory"]["summary"], "case_version": result["final_case_version"],
        "idempotent_retry": result["idempotent_retry_kept_version"], "history": result["history_versions"],
        "note": result["note"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
