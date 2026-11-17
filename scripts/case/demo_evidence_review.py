"""Synthetic feature-3 follow-up: link existing materials, supplement, confirm duplication."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.case import CaseService, SQLiteCaseRepository, load_development_case
from backend.case.models import CaseSnapshot, Claim, Confirmation
from backend.agent.case_adapter import RepositoryCasePort


REPAIR_TEXT = ("【合成开发材料，非真实票据】\n票据编号：SYN-REPAIR-001\n维修金额：3000.00元\n"
               "维修项目：更换测试车辆前部受损部件，核对原始维修明细、维修时间以及实际支付情况。\n"
               "备注：此材料仅用于开发测试，需要人工核对票据编号、原图、费用项目和案件关联，不证明真实性或法律证明力。")


def run_demo(directory):
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("choose a new or empty output directory")
    directory.mkdir(parents=True, exist_ok=True)
    repo = SQLiteCaseRepository(directory / "demo.sqlite3")
    payload = load_development_case().model_dump(mode="json")
    payload.update(case_id="synthetic_feature03_review", evidence=[], claims=[])
    payload["facts"] = [payload["facts"][0], payload["facts"][2]]
    case = repo.create_case(CaseSnapshot.model_validate(payload))
    service = CaseService(repo)

    def context(key):
        return {"request_id": key, "case_id": case.case_id, "experiment_run_id": "synthetic-feature03", "deadline_at": None}

    def current():
        return repo.get_case(case.case_id)

    def check(key):
        snapshot = current()
        return service.check_evidence({"context": context(key), "case_version": snapshot.case_version, "case_snapshot": snapshot})

    def upload(file_id, kind, text):
        (directory / (file_id+".txt")).write_text(text, encoding="utf-8")
        service.assets.put_file(case_id=case.case_id, file_id=file_id, file_version="1", file_name=file_id+".txt",
            media_type="text/plain", document_type=kind, content=text.encode())

    def parse(key, file_ids):
        result = service.parse_documents({"context": context(key), "case_version": current().case_version, "file_ids": file_ids})
        if result.data is None:
            raise RuntimeError(result.model_dump_json())
        return result.data

    def save(key, changes, reason):
        result = service.update_case({"context": context(key), "expected_case_version": current().case_version,
                                      "changes": changes, "change_reason": reason})
        if result.status != "ok":
            raise RuntimeError(result.model_dump_json())
        return result.data.case_snapshot

    upload("repair_a", "repair_invoice", REPAIR_TEXT)
    upload("repair_b", "repair_invoice", REPAIR_TEXT.replace("\n", "  \n"))
    upload("medical_invoice", "medical_invoice", "【合成医疗票据，非真实票据】\n医疗费：120.00元\n")
    parsed = parse("parse-initial", ["repair_a", "repair_b", "medical_invoice"])
    items = {item.file_id: item for item in parsed.documents}
    repair_claim = Claim(claim_id="claim_repair", kind="repair_cost", description="合成维修费用请求",
        amount={"amount": "3000.00", "currency": "CNY"},
        fact_ids=items["repair_a"].proves_fact_ids + items["repair_b"].proves_fact_ids,
        evidence_ids=[items["repair_a"].evidence_id, items["repair_b"].evidence_id])
    medical_claim = Claim(claim_id="claim_medical", kind="medical_cost", description="合成医疗费用请求",
        amount={"amount": "120.00", "currency": "CNY"}, fact_ids=items["medical_invoice"].proves_fact_ids,
        evidence_ids=[])  # Uploaded already, but deliberately not linked to this claim yet.
    initial = save("save-initial", {"upsert_evidence": parsed.documents, "upsert_facts": parsed.extracted_facts,
                                   "upsert_claims": [repair_claim, medical_claim]}, "synthetic candidates; no auto-confirmation")
    before = check("check-before")
    unchanged_by_check = current() == initial

    # The remainder explicitly simulates user actions. It is not Agent autonomy.
    upload("clinical_record", "medical_record", "【合成诊疗记录，非真实病历】\n事故日期：2026-05-12\n诊疗内容仅用于程序演示。\n")
    clinical = parse("parse-supplement", ["clinical_record"])
    save("save-supplement", {"upsert_evidence": clinical.documents, "upsert_facts": clinical.extracted_facts}, "synthetic user supplements clinical record")
    reply = RepositoryCasePort(repo).record_user_reply(case_id=case.case_id, expected_case_version=current().case_version,
        request_id="record-explicit-review", message_id="msg_feature03_review", actor_id="synthetic_user",
        text="合成确认：repair_a和repair_b是同一张票据，保留repair_a，排除repair_b；把已上传医疗票据及新增诊疗记录关联到医疗费诉求。",
        created_at=datetime.now(timezone.utc))
    if reply.status != "ok":
        raise RuntimeError(reply.model_dump_json())
    snapshot = current()
    message = next(item for item in snapshot.statements if item.message_id == "msg_feature03_review")
    excluded = items["repair_b"].model_copy(update={"status": "excluded", "duplicate_of": items["repair_a"].evidence_id,
        "exclusion": Confirmation(message_id=message.message_id, confirmed_by=message.actor_id, confirmed_at=message.created_at)})
    linked_medical = medical_claim.model_copy(update={"evidence_ids": [items["medical_invoice"].evidence_id, clinical.documents[0].evidence_id]})
    linked_repair = repair_claim.model_copy(update={"evidence_ids": [items["repair_a"].evidence_id],
        "fact_ids": items["repair_a"].proves_fact_ids})
    reviewed = save("apply-explicit-review", {"upsert_evidence": [excluded], "upsert_claims": [linked_medical, linked_repair]},
                    "apply only the synthetic user's explicit selection and associations")
    after = check("check-after")
    unchanged_after_check = current() == reviewed

    RepositoryCasePort(repo).set_stage(case.case_id, "litigation")  # Synthetic explicit route selection.
    litigation = current()
    directory_result = service.build_evidence_directory({"context": context("directory"),
        "case_version": litigation.case_version, "case_snapshot": litigation})
    checks = {
        "similar_text_candidate_reported": any(item.finding_id.startswith("similar_") for item in before.data.duplicates),
        "existing_unlinked_invoice_located": any("已有此类型材料" in item.message and items["medical_invoice"].evidence_id in item.evidence_ids for item in before.data.material_gaps),
        "missing_clinical_record_reported": any("诊疗记录" in item.message for item in before.data.material_gaps),
        "medical_gaps_resolved_after_explicit_updates": not any("医疗" in item.message or "诊疗记录" in item.message for item in after.data.material_gaps),
        "duplicates_resolved_after_explicit_exclusion": not after.data.duplicates,
        "checks_do_not_mutate_cases": unchanged_by_check and unchanged_after_check,
        "original_snapshot_kept": repo.get_case(case.case_id, initial.case_version) == initial,
        "original_material_kept": service.assets.get_file(case.case_id, "repair_b", "1")[1] == REPAIR_TEXT.replace("\n", "  \n").encode(),
        "claim_amounts_unchanged": [claim.amount for claim in initial.claims] == [claim.amount for claim in reviewed.claims],
        "directory_records_exclusion": items["repair_b"].evidence_id in directory_result.data.excluded_evidence_ids,
        "other_missing_facts_stay_visible": bool(after.data.missing_facts),
    }
    report = {"scope": "Synthetic development example; explicit user actions are simulated, no LLM or independent legal evaluation.",
              "checks_passed": all(checks.values()), "checks": checks,
              "before": before.model_dump(mode="json"), "after": after.model_dump(mode="json"),
              "directory": directory_result.model_dump(mode="json"), "final_case_version": current().case_version}
    (directory / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    report = run_demo(parser.parse_args().output_dir)
    print(json.dumps({"checks_passed": report["checks_passed"], "checks": report["checks"],
                      "final_case_version": report["final_case_version"]}, ensure_ascii=False, indent=2))
    raise SystemExit(not report["checks_passed"])
