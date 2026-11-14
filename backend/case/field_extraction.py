"""Conservative, labelled-field extraction with offsets into unchanged source text."""

from __future__ import annotations

import hashlib
import re
from datetime import date
from decimal import Decimal

from .models import Fact


FIELD_EXTRACTION_VERSION = "case-document-fields-0.2.0"

# Roles must appear explicitly; a driver's name is not assumed to be the claimant.
FIELD_RULES = (
    ("accident_date", "date", ("事故发生日期", "事故日期")),
    ("accident_location", "text", ("事故发生地点", "事故地点")),
    ("claimant_name", "text", ("原告姓名", "原告名称", "原告")),
    ("defendant_name", "text", ("被告姓名", "被告名称", "被告")),
    ("insurance", "text", ("保险关系", "保险情况")),
    ("liability_finding", "text", ("责任认定", "责任记载")),
    ("repair_amount", "money", ("维修金额", "维修费用", "维修费", "修理费")),
    ("medical_amount", "money", ("医疗金额", "医疗费用", "医疗费")),
    ("towing_amount", "money", ("拖车费",)),
    ("appraisal_amount", "money", ("鉴定费",)),
)
_LABELS = {label: (field, kind) for field, kind, labels in FIELD_RULES for label in labels}
# Every translation is one character to one character, preserving source offsets.
_WIDTH = {value: chr(value - 0xFEE0) for value in range(0xFF01, 0xFF5F)}
_WIDTH[0x3000] = " "
_HEADER = re.compile(
    r"(?<!\w)(?P<label>" + "|".join(sorted(_LABELS, key=len, reverse=True))
    + r")(?:[ \t]*:[ \t]*(?:\r?\n[ \t]*)?|\t+[ \t]*)"
)
_END = re.compile(r"[\r\n;；\t]")
_MONEY = re.compile(
    r"(?:(?:人民币|CNY|RMB)[ ]*)?[¥￥]?[ ]*"
    r"(?P<number>(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]{1,2})?)"
    r"[ ]*(?:人民币元|元)?"
)


def _groups(segments):
    """Join only contiguous segments from the very same page/extraction."""
    group = []
    previous = None
    for segment in segments:
        ref = segment.material_ref
        key = (ref.file_id, ref.file_version, ref.page, ref.extraction_version)
        if previous is not None:
            prev = previous.material_ref
            prev_key = (prev.file_id, prev.file_version, prev.page, prev.extraction_version)
            if key != prev_key or ref.span_start != prev.span_end + 1:
                yield group
                group = []
        group.append(segment)
        previous = segment
    if group:
        yield group


def _date_value(value):
    numeric = re.fullmatch(r"([0-9]{4})([-/])([0-9]{1,2})\2([0-9]{1,2})", value)
    chinese = re.fullmatch(r"([0-9]{4})年[ ]*([0-9]{1,2})月[ ]*([0-9]{1,2})日?", value)
    if numeric:
        return date(int(numeric[1]), int(numeric[3]), int(numeric[4])).isoformat()
    if chinese:
        return date(*map(int, chinese.groups())).isoformat()
    raise ValueError("日期格式或日历日期不明确")


def extract_labelled_fields(record, evidence_id, segments):
    """Return unconfirmed facts plus diagnostics; never write a case snapshot.

    This is a small deterministic parser, not general document comprehension.
    A value can occupy the line immediately following its label on the same page.
    """
    facts, warnings = [], []
    for group in _groups(segments):
        original = "\n".join(segment.text for segment in group)
        normalized = original.translate(_WIDTH)
        base = group[0].material_ref.span_start
        headers = list(_HEADER.finditer(normalized))
        for index, header in enumerate(headers):
            field, kind = _LABELS[header["label"]]
            start = header.end()
            boundary = _END.search(normalized, start)
            end = boundary.start() if boundary else len(normalized)
            if index + 1 < len(headers):
                end = min(end, headers[index + 1].start())
            while start < end and normalized[start].isspace():
                start += 1
            while end > start and normalized[end - 1].isspace():
                end -= 1
            value = normalized[start:end]
            try:
                if not value:
                    raise ValueError("字段值为空")
                if kind == "date":
                    value = _date_value(value)
                elif kind == "money":
                    money = _MONEY.fullmatch(value)
                    if money is None:
                        raise ValueError("金额格式、单位或币种不明确，不截取局部数字")
                    value = {"amount": format(Decimal(money["number"].replace(",", "")), "f"), "currency": "CNY"}
                    end = start + money.end("number")
                    start += money.start("number")
                else:
                    # Text fields keep the original characters, including fullwidth names.
                    value = original[start:end]
            except ValueError as exc:
                page = group[0].material_ref.page
                location = f"第{page}页" if page is not None else "文字区"
                warnings.append(f"{record.file_id}/{location}/{header['label']}: {exc}，请核对原文。")
                continue
            absolute_start, absolute_end = base + start, base + end
            # Value coordinates must come from its own OCR line, never the label line.
            owner = next((segment for segment in group
                          if segment.material_ref.span_start <= absolute_start
                          and absolute_end <= segment.material_ref.span_end), None)
            if owner is None:
                warnings.append(f"{record.file_id}/{header['label']}: 字段跨越多个识别区域，需要人工核对。")
                continue
            ref = owner.material_ref.model_copy(update={
                "span_start": absolute_start, "span_end": absolute_end, "quote": original[start:end]})
            fact_field = f"losses.{record.file_id}.{field}" if kind == "money" else field
            identity = hashlib.sha256(
                f"{evidence_id}:{fact_field}:{ref.extraction_version}:{ref.page}:{absolute_start}:{absolute_end}".encode()
            ).hexdigest()[:20]
            facts.append(Fact(fact_id="f_" + identity, field=fact_field, value_type=kind, value=value,
                              provenance="document_record", confirmation_status="unconfirmed",
                              evidence_ids=[evidence_id], material_refs=[ref]))
    return facts, list(dict.fromkeys(warnings))
