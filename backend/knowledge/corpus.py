"""Shared provision loading and index input formatting."""
import json
from pathlib import Path

PROVISION_FIELDS = (
    "provision_id", "law_id", "law_title", "article_no", "hierarchy_path",
    "text", "source_file", "source_url", "page_start", "page_end",
    "publication_date", "effective_date", "expiry_date", "status",
)


def load_provisions(jsonl_paths: list[Path]) -> list[dict]:
    provisions = []
    for path in jsonl_paths:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if line.strip():
                record = json.loads(line)
                provisions.append({field: record.get(field) for field in PROVISION_FIELDS})
    if not provisions or len({p["provision_id"] for p in provisions}) != len(provisions):
        raise ValueError("Corpus must be nonempty with unique provision IDs")
    return provisions


def provision_text(provision: dict, include_metadata: bool) -> str:
    if include_metadata:
        return f"{provision['law_title']} {' '.join(provision['hierarchy_path'] or [])} {provision['text']}"
    return provision["text"]
