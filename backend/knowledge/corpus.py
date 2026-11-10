"""Shared provision loading and index input formatting."""
import json
from datetime import date
from pathlib import Path
import re

from langchain_core.documents import Document

PROVISION_FIELDS = (
    "provision_id", "law_id", "law_title", "article_no", "hierarchy_path",
    "text", "source_file", "source_url", "page_start", "page_end",
    "publication_date", "effective_date", "expiry_date", "status",
)


def _validate_record(record: dict) -> None:
    """Check retrieval inputs, without inferring dates or legal validity."""
    for field in ("provision_id", "law_id", "law_title", "article_no", "text",
                  "source_file", "source_url", "status"):
        if not isinstance(record.get(field), str) or not record[field].strip():
            raise ValueError(f"{field} must be a nonempty string")
    hierarchy = record.get("hierarchy_path")
    if not isinstance(hierarchy, list) or any(
            not isinstance(part, str) or not part.strip() for part in hierarchy):
        raise ValueError("hierarchy_path must be a list of nonempty strings (an empty list is allowed)")
    start, end = record.get("page_start"), record.get("page_end")
    if (start, end) != (None, None):
        if any(type(page) is not int or page < 1 for page in (start, end)):
            raise ValueError("page_start and page_end must both be positive integers or both null")
        if start > end:
            raise ValueError("page_start must not exceed page_end")
    for field in ("publication_date", "effective_date", "expiry_date"):
        value = record.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError(f"{field} must be a YYYY-MM-DD date or null")
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ValueError(f"{field} is not a valid calendar date") from None


def load_provisions(jsonl_paths: list[Path]) -> list[dict]:
    """Fail on the first bad record, reporting its file and physical line."""
    provisions = []
    seen = {}
    for path in jsonl_paths:
        found_record = False
        for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            if line.strip():
                location = f"{path}:{line_no}"
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{location}: invalid JSON: {exc.msg} (column {exc.colno})") from None
                if not isinstance(record, dict):
                    raise ValueError(f"{location}: record must be a JSON object")
                try:
                    _validate_record(record)
                except ValueError as exc:
                    raise ValueError(f"{location}: {exc}") from None
                pid = record["provision_id"]
                if pid in seen:
                    raise ValueError(f"{location}: duplicate provision_id {pid!r}; first seen at {seen[pid]}")
                seen[pid] = location
                provisions.append({field: record.get(field) for field in PROVISION_FIELDS})
                found_record = True
        if not found_record:
            raise ValueError(f"{path}:1: JSONL file contains no provision records")
    if not provisions:
        raise ValueError("Corpus must contain at least one JSONL file with provision records")
    return provisions


def provision_text(provision: dict, include_metadata: bool) -> str:
    if include_metadata:
        return f"{provision['law_title']} {' '.join(provision['hierarchy_path'] or [])} {provision['text']}"
    return provision["text"]


def provision_document(provision: dict, include_metadata: bool = False) -> Document:
    """Index text is explicit; retain original text separately for legal quotes."""
    return Document(id=provision["provision_id"],
                    page_content=provision_text(provision, include_metadata),
                    metadata=dict(provision))
