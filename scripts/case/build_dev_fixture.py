"""Rebuild the public, synthetic phase-one fixture; requires no model or network."""

from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.case.models import BusinessOutput, CaseSnapshot  # noqa: E402
from backend.case.contracts import CheckEvidenceRequest, InvocationContext  # noqa: E402


def write_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    directory = ROOT / "data/examples/member3/traffic_accident_v1"
    materials = directory / "materials"
    materials.mkdir(parents=True, exist_ok=True)
    texts = {
        "accident_record.txt": "【合成开发材料，非真实事故认定书】\n事故日期：2026-05-13\n当事人：示例甲与示例乙\n责任记载：示例乙承担全部责任（仅为合成文本）。\n",
        "repair_invoice.txt": "【合成开发材料，非真实票据】\n票据编号：SYN-REPAIR-001\n维修日期：2026-05-15\n维修金额：3000.00元\n",
    }
    texts["repair_invoice_copy.txt"] = texts["repair_invoice.txt"]
    for name, text in texts.items():
        (materials / name).write_bytes(text.encode("utf-8"))

    def ref(file_id, name, quote):
        start = texts[name].index(quote)
        return {"file_id": file_id, "file_version": "1", "page": None,
                "paragraph": None, "span_start": start, "span_end": start + len(quote), "quote": quote}

    accident_ref = ref("file_accident", "accident_record.txt", "事故日期：2026-05-13")
    invoice_ref = ref("file_invoice", "repair_invoice.txt", "维修金额：3000.00元")
    copy_ref = ref("file_invoice_copy", "repair_invoice_copy.txt", "维修金额：3000.00元")
    timestamp = "2026-11-01T08:00:00Z"
    evidence = []
    for evidence_id, file_id, name, kind, location, fact_id in [
        ("e_accident", "file_accident", "accident_record.txt", "accident_record", accident_ref, "f_date_document"),
        ("e_invoice", "file_invoice", "repair_invoice.txt", "repair_invoice", invoice_ref, "f_repair_amount"),
        ("e_invoice_copy", "file_invoice_copy", "repair_invoice_copy.txt", "repair_invoice", copy_ref, "f_repair_amount_copy"),
    ]:
        evidence.append({"evidence_id": evidence_id, "file_id": file_id, "file_version": "1", "file_name": name,
                         "media_type": "text/plain", "document_type": kind,
                         "content_sha256": hashlib.sha256(texts[name].encode("utf-8")).hexdigest(),
                         "material_refs": [location], "proves_fact_ids": [fact_id],
                         "status": "unreviewed", "duplicate_of": None, "warnings": []})
    facts = [
        {"fact_id": "f_date_user", "field": "accident_date", "value_type": "date", "value": "2026-05-12",
         "provenance": "user_statement", "confirmation_status": "unconfirmed", "statement_refs": ["msg_001"]},
        {"fact_id": "f_date_document", "field": "accident_date", "value_type": "date", "value": "2026-05-13",
         "provenance": "document_record", "confirmation_status": "unconfirmed",
         "evidence_ids": ["e_accident"], "material_refs": [accident_ref]},
        {"fact_id": "f_claimant", "field": "claimant_name", "value_type": "text", "value": "示例甲",
         "provenance": "user_statement", "confirmation_status": "confirmed", "statement_refs": ["msg_001"],
         "confirmation": {"message_id": "msg_001", "confirmed_by": "synthetic_user", "confirmed_at": timestamp}},
    ]
    for fact_id, evidence_id, location in [("f_repair_amount", "e_invoice", invoice_ref),
                                           ("f_repair_amount_copy", "e_invoice_copy", copy_ref)]:
        facts.append({"fact_id": fact_id, "field": "repair_amount", "value_type": "money",
                      "value": {"amount": "3000.00", "currency": "CNY"}, "provenance": "document_record",
                      "confirmation_status": "unconfirmed", "evidence_ids": [evidence_id], "material_refs": [location]})
    snapshot = CaseSnapshot.model_validate({
        "case_id": "synthetic_traffic_001", "case_version": 1, "synthetic": True,
        "dispute_type": "traffic_accident", "jurisdiction": "CN-Mainland", "stage": "consultation",
        "selected_route": None, "user_goals": ["先了解处理方式，再决定是否诉讼"],
        "statements": [{"message_id": "msg_001", "actor_id": "synthetic_user",
                        "text": "我是示例甲，我确认这个称呼。我记得事故发生在2026年5月12日，想主张维修费。", "created_at": timestamp}],
        "facts": facts, "evidence": evidence,
        "claims": [{"claim_id": "claim_repair", "kind": "repair_cost", "description": "请求维修费用赔偿",
                    "amount": {"amount": "3000.00", "currency": "CNY"}, "status": "proposed",
                    "fact_ids": ["f_date_user", "f_date_document", "f_repair_amount"],
                    "evidence_ids": ["e_accident", "e_invoice", "e_invoice_copy"], "source_refs": []}],
        "created_at": timestamp, "updated_at": timestamp,
    })
    write_json(directory / "case_snapshot.json", snapshot.model_dump(mode="json"))
    context = InvocationContext(request_id="dev-request-001", case_id=snapshot.case_id,
                                experiment_run_id="development-only", deadline_at=None)
    request = CheckEvidenceRequest(context=context, case_version=1, case_snapshot=snapshot)
    write_json(directory / "check_evidence_request.json", request.model_dump(mode="json"))
    invalid = deepcopy(snapshot.model_dump(mode="json"))
    invalid["facts"][1]["evidence_ids"] = ["e_does_not_exist"]
    write_json(directory / "invalid_case_snapshot.json", invalid)
    write_json(directory / "expected_review.json", {
        "development_only": True, "authored_by": "member3_contract_fixture", "not_tool_execution": True,
        "expected_missing_fields": ["insurance", "liability_finding"],
        "expected_conflict": {"field": "accident_date", "fact_ids": ["f_date_user", "f_date_document"]},
        "expected_duplicate_group": ["e_invoice", "e_invoice_copy"],
        "expected_unique_invoice_amount": {"amount": "3000.00", "currency": "CNY"},
        "notes": ["以上为公开开发样例的人工预期，不是程序已经发现的问题。",
                  "不应把两份相同票据相加为6000元。",
                  "金额只用于结构与去重测试，不表达法律上可获支持的金额。"],
    })
    output = BusinessOutput(
        output_id="example_structure_only", output_version=1, case_id=snapshot.case_id,
        case_version=1, output_type="analysis", review_status="unreviewed",
        text="【人工编写的接口样例】事故日期存在两种记载，尚待用户确认。",
        missing_fields=["insurance"],
        links=[{"output_location": "paragraph:1", "fact_ids": ["f_date_user", "f_date_document"],
                "evidence_ids": ["e_accident"], "source_refs": []}],
    )
    write_json(directory / "example_output.json", output.model_dump(mode="json"))
    print(f"Synthetic fixture built: {directory}")


if __name__ == "__main__":
    main()
