"""Validate a local download manifest and import an immutable official form library."""
import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
SUPPORTED = {
    "机动车交通事故责任纠纷民事起诉状": "traffic_accident",
    "民间借贷纠纷民事起诉状": "private_lending",
    "买卖合同纠纷民事起诉状": "sales_contract",
}
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def fields_from_xml(xml):
    fields = []
    last_label = ""
    section = "当事人信息"
    for ti, table in enumerate(xml.findall("./w:body/w:tbl", NS)):
        for ri, row in enumerate(table.findall("w:tr", NS)):
            cells = row.findall("w:tc", NS)
            texts = ["\n".join("".join(p.itertext()) for p in c.findall("w:p", NS)).strip() for c in cells]
            label = re.sub(r"\s+", "", texts[0])
            if len(cells) == 1:
                if label in ("当事人信息", "事实与理由", "约定管辖和诉前保全", "诉前保全及鉴定申请", "对纠纷解决方式的意愿") or label.startswith("诉讼请求"):
                    section = label.split("（")[0]
                continue
            if label:
                last_label = label
            for ci in range(1, len(cells)):
                fields.append({"field_id": f"t{ti}_r{ri}_c{ci}", "label": last_label,
                    "section": section, "original_prompt": texts[ci],
                    "table": ti, "row": ri, "cell": ci,
                    "fill_mode": "append_confirmed_text", "requires_user_review": True})
    return fields


def import_library(source, destination):
    source = source.resolve()
    destination = destination.resolve()
    manifest_path = source / "下载清单.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records, payloads = [], []
    for item in manifest["items"]:
        if item.get("status") != "downloaded":
            raise ValueError("download manifest contains unfinished items")
        path = (source / item["file"]).resolve()
        if not path.is_relative_to(source / "原始附件"):
            raise ValueError("source file escapes originals directory")
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != item["sha256"] or len(data) != item["bytes"]:
            raise ValueError("download hash/length mismatch: " + path.name)
        with ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > 10000 or sum(i.file_size for i in infos) > 100 * 1024 * 1024:
                raise ValueError("oversized DOCX package")
            if archive.testzip() is not None or "[Content_Types].xml" not in archive.namelist():
                raise ValueError("invalid DOCX package: " + path.name)
            xml = ET.fromstring(archive.read("word/document.xml"))
        title = item["title"].removesuffix(".docx")
        example = title.endswith("实例")
        base_title = title.removesuffix("实例")
        template_id = "bjcourt_" + hashlib.sha256(base_title.encode()).hexdigest()[:16]
        version = "sha256_" + digest[:16]
        kind = "example" if example else "blank"
        relative = f"{kind}/{template_id}/{version}.docx"
        domain = next((key for key in ("行政", "刑事", "国家赔偿", "民事") if key in title), "其他程序")
        record = {"template_id": template_id, "version": version, "title": title, "kind": kind,
            "domain": domain, "document_type": "complaint" if "起诉状" in title else "defense" if "答辩状" in title else "other",
            "dispute_type": SUPPORTED.get(base_title), "filling_supported": not example and base_title in SUPPORTED,
            "path": relative, "sha256": digest, "bytes": len(data), "source_url": item["url"],
            "source_page": manifest["source_page"], "downloaded_at": item.get("downloaded_at"),
            "validation": "sha256_zip_crc_document_xml", "fields": fields_from_xml(xml) if not example and base_title in SUPPORTED else []}
        records.append(record)
        payloads.append((relative, data))
    catalog = {"schema_version": "1.0", "source_page": manifest["source_page"],
               "records": records, "counts": {"files": len(records),
               "blank": sum(r["kind"] == "blank" for r in records), "example": sum(r["kind"] == "example" for r in records)}}
    if destination.exists():
        current = json.loads((destination / "catalog.json").read_text(encoding="utf-8"))
        if current != catalog or any((destination / name).read_bytes() != data for name, data in payloads):
            raise ValueError("destination already contains a different or modified library")
        return catalog
    temporary = destination.with_name(destination.name + ".importing")
    temporary.mkdir(parents=True, exist_ok=False)
    try:
        for relative, data in payloads:
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (temporary / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.rename(destination)
    except BaseException:
        if temporary.resolve().parent == destination.parent and temporary.name == destination.name + ".importing":
            shutil.rmtree(temporary)
        raise
    return catalog


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, help="folder containing 下载清单.json")
    parser.add_argument("--destination", type=Path, default=ROOT / "data/templates/official/bjcourt")
    args = parser.parse_args()
    print(json.dumps(import_library(args.source, args.destination)["counts"], ensure_ascii=False))
