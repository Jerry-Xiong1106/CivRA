"""Synthetic text/Word and optional real PaddleOCR parsing through the public Python interface."""

import argparse
import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.case import CaseService, SQLiteCaseRepository
from scripts.case.fixtures import load_development_case
from backend.case.materials.field_extraction import FIELD_EXTRACTION_VERSION
from backend.case.materials.ocr import OcrEngineRegistry


LINES = ["事故日期：2026-05-12", "事故地点：测试路口", "原告姓名：甲某", "被告姓名：乙某",
         "保险关系：待核对", "责任认定：待核对", "维修费：3000.00元", "医疗费：100.00元",
         "拖车费：50.00元", "鉴定费：80.00元"]
EXPECTED = {"accident_date": "2026-05-12", "accident_location": "测试路口", "claimant_name": "甲某",
            "defendant_name": "乙某", "insurance": "待核对", "liability_finding": "待核对",
            **{name: {"amount": amount, "currency": "CNY"} for name, amount in [
                ("repair_amount", "3000.00"), ("medical_amount", "100.00"),
                ("towing_amount", "50.00"), ("appraisal_amount", "80.00")]}}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True, help="New or empty directory; results are retained")
    parser.add_argument("--ocr-config", type=Path, help="Optional existing engine config; enables actual image OCR")
    parser.add_argument("--font", type=Path, default=Path("C:/Windows/Fonts/msyh.ttc"))
    args = parser.parse_args()
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        parser.error("output directory must be empty; use a new directory for each run")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    engine = None
    if args.ocr_config:
        config = json.loads(args.ocr_config.read_text(encoding="utf-8-sig"))
        engine = OcrEngineRegistry().create(config["engine"], config["options"])
    repo = SQLiteCaseRepository(args.output_dir / "demo.sqlite3")
    case = repo.create_case(load_development_case())
    service = CaseService(repo, ocr=engine)
    files = [("text", "sample.txt", "text/plain", "\n".join(LINES).encode())]
    from docx import Document
    doc = Document()
    doc.add_paragraph(LINES[0])
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = LINES[1].split("：", 1)
    for line in LINES[2:]:
        doc.add_paragraph(line)
    buffer = io.BytesIO()
    doc.save(buffer)
    files.append(("word", "sample.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", buffer.getvalue()))
    if engine is not None:
        from PIL import Image, ImageDraw, ImageFont
        image = Image.new("RGB", (1300, 850), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(str(args.font), 36)
        for index, line in enumerate(["合成打印样例，仅开发验证"] + LINES):
            draw.text((45, 25 + index * 70), line, font=font, fill="black")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        files.append(("printed", "printed.png", "image/png", buffer.getvalue()))
    records = []
    for file_id, filename, media, content in files:
        (args.output_dir / filename).write_bytes(content)
        service.assets.put_file(case_id=case.case_id, file_id=file_id, file_version="1", file_name=filename,
                                media_type=media, document_type="other", content=content)
        started = time.monotonic()
        result = service.parse_documents({"context": {"request_id": "demo-" + file_id,
            "case_id": case.case_id, "experiment_run_id": "synthetic-documents-demo", "deadline_at": None},
            "case_version": case.case_version, "file_ids": [file_id], "ocr_mode": "auto"})
        facts = result.data.extracted_facts if result.data else []
        values = {fact.field.removeprefix("losses." + file_id + "."): fact.value for fact in facts}
        sources_ok = all(service.assets.read_extraction(case.case_id, ref) == ref.quote
                         for fact in facts for ref in fact.material_refs)
        records.append({"file_id": file_id, "elapsed_ms": round((time.monotonic()-started)*1000),
            "expected_fields_match": values == EXPECTED, "source_quotes_verified": sources_ok,
            "all_candidates_unconfirmed": all(fact.confirmation_status == "unconfirmed" for fact in facts),
            "result": result.model_dump(mode="json")})
    unchanged = repo.get_case(case.case_id) == case
    passed = unchanged and all(record["expected_fields_match"] and record["source_quotes_verified"]
                              and record["all_candidates_unconfirmed"] for record in records)
    report = {"scope": "Synthetic printed development examples only; not a handwriting or independent accuracy evaluation.",
              "field_extraction_version": FIELD_EXTRACTION_VERSION, "real_ocr_enabled": engine is not None,
              "case_snapshot_unchanged": unchanged, "checks_passed": passed, "records": records}
    (args.output_dir / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"checks_passed": passed, "case_snapshot_unchanged": unchanged,
        "files": [{key: value for key, value in record.items() if key != "result"} for record in records]},
        ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
