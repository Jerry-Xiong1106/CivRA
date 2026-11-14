"""Synthetic intake review using real business code; no LLM or frontend."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.case import CaseService, SQLiteCaseRepository, load_development_case
from backend.case.contracts import CaseChanges, CaseRequest, InvocationContext, UpdateCaseRequest
from backend.case.intake import CaseIntakeService, FactReviewRequest
from backend.case.models import UserStatement


def run_demo(directory):
    repo = SQLiteCaseRepository(Path(directory) / "intake.sqlite3")
    case = repo.create_case(load_development_case())
    service, intake = CaseService(repo), CaseIntakeService(repo)

    def context(key):
        return InvocationContext(request_id=key, case_id=case.case_id,
                                 experiment_run_id="synthetic-intake-demo", deadline_at=None)

    before = intake.summarize(CaseRequest(context=context("summary-before"), case_version=case.case_version))
    # The demo stands in for an authenticated application's message save.
    reply = UserStatement(message_id="intake_demo_reply", actor_id="synthetic_user",
        text="合成确认：事故日期采用用户陈述中的日期，并显式将另一日期候选记为已修正。", created_at=datetime.now(timezone.utc))
    saved = service.update_case(UpdateCaseRequest(context=context("record-reply"), expected_case_version=case.case_version,
        changes=CaseChanges(upsert_statements=[reply]), change_reason="synthetic application recorded reply"))
    if saved.status != "ok":
        raise RuntimeError(saved.model_dump_json())
    prepared = intake.prepare_review(FactReviewRequest(context=context("review"), case_version=saved.case_version,
        message_id=reply.message_id, reviewer_id=reply.actor_id,
        actions=[{"fact_id":"f_date_user", "action":"confirm", "supersede_fact_ids":["f_date_document"]}]))
    if prepared.status != "ok" or prepared.data.update_request is None:
        raise RuntimeError(prepared.model_dump_json())
    committed = service.update_case(prepared.data.update_request)
    if committed.status != "ok":
        raise RuntimeError(committed.model_dump_json())
    after = intake.summarize(CaseRequest(context=context("summary-after"), case_version=committed.case_version))
    return {"scope":"synthetic explicit review; no natural-language interpretation or LLM",
        "before":before.model_dump(mode="json"), "prepared":prepared.model_dump(mode="json"),
        "after":after.model_dump(mode="json"), "final_case_version":committed.case_version}


if __name__ == "__main__":
    with TemporaryDirectory(prefix="civra-intake-demo-") as directory:
        print(json.dumps(run_demo(directory), ensure_ascii=False, indent=2))
