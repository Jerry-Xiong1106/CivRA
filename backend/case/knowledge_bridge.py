"""Read-only bridge to Member 2's versioned corpus; never performs retrieval."""
from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path

from .models import SourcePointer


def normalize_dispute_type(value):
    return {"lending": "private_lending", "loan": "private_lending"}.get(value, value)


class CorpusSourceResolver:
    """Pin an in-memory corpus snapshot and resolve exact ID/effective-date pairs.

    The version convention matches the current evaluation and statute-tool adapters.
    A manifest hash additionally identifies the exact content, not legal currency.
    """
    def __init__(self, provisions):
        self._records = {}
        for raw in provisions:
            text = raw["text"]
            if not isinstance(text, str) or not text.strip():
                raise ValueError("empty provision text")
            for key in ("provision_id", "law_title", "article_no", "effective_date", "source_file"):
                if not isinstance(raw.get(key), str) or not raw[key].strip():
                    raise ValueError("missing corpus identity/locator: " + key)
            date.fromisoformat(raw["effective_date"])
            if raw.get("expiry_date"):
                date.fromisoformat(raw["expiry_date"])
            if type(raw["page_start"]) is not int or type(raw["page_end"]) is not int or not 1 <= raw["page_start"] <= raw["page_end"]:
                raise ValueError("invalid PDF page range")
            key = (raw["provision_id"], raw["effective_date"])
            if key in self._records:
                raise ValueError("duplicate source ID/version in corpus")
            self._records[key] = dict(
                source_id=key[0], version=key[1], source_type="statute", title=raw["law_title"],
                locator=raw["article_no"], text=text, excerpt=text, url=raw.get("source_url"),
                effective_from=key[1], effective_to=raw.get("expiry_date"), verified_on=None,
                text_scope="full_provision", source_file=raw["source_file"],
                page_start=raw["page_start"], page_end=raw["page_end"],
                publication_date=raw.get("publication_date"), registered_status=raw.get("status"),
                text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            )
        if not self._records:
            raise ValueError("empty corpus snapshot")
        payload = json.dumps([self._records[k] for k in sorted(self._records)], ensure_ascii=False, sort_keys=True)
        self.snapshot_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def from_directory(cls, directory=None):
        root = Path(directory) if directory is not None else Path(__file__).resolve().parents[2] / "data/processed"
        # These are the three explicit inputs published by Member 2, not a directory crawl.
        records = []
        for name in ("civil_code.jsonl", "civil_procedure_law.jsonl", "road_traffic_safety_law.jsonl"):
            records.extend(json.loads(line) for line in (root / name).read_text(encoding="utf-8").splitlines() if line.strip())
        return cls(records)

    def __call__(self, pointer):
        pointer = SourcePointer.model_validate(pointer)
        key = (pointer.source_id, pointer.source_version)
        if key not in self._records:
            raise KeyError("source ID/version is absent from the pinned corpus snapshot")
        return deepcopy(self._records[key])

    def manifest(self):
        return {"version_convention": "effective_date", "snapshot_sha256": self.snapshot_hash,
                "source_count": len(self._records), "current_legal_validity_verified": False,
                "capabilities": {"search_cases": False, "date_filter": False, "multi_version": False},
                "limitations": ["仅读取当前快照，登记状态不代表已核验现行效力。",
                                "部分链接为官方数据库首页，PDF页码定位于随库文件。"]}


def executable_research_plan(plan, *, expected_snapshot=None):
    """Convert a Member 3 research plan to the current query/top_k API.

    Preserve unsupported requirements explicitly; do not claim to apply filters.
    The caller executes these requests through Member 2/4's registered tool.
    """
    requests, deferred = [], []
    for query in plan.queries:
        if query.tool != "search_statutes":
            deferred.append({"query_id": query.query_id, "reason": "case_retrieval_not_available"})
            continue
        if expected_snapshot is not None and query.knowledge_version != expected_snapshot:
            deferred.append({"query_id": query.query_id, "reason": "knowledge_snapshot_mismatch"})
            continue
        requests.append({"query_id": query.query_id, "tool": "search_statutes",
                         "arguments": {"query": query.query, "top_k": query.top_k},
                         "unapplied_filters": deepcopy(query.filters),
                         "requested_knowledge_version": query.knowledge_version})
    return {"requests": requests, "deferred": deferred,
            "limitations": ["没有执行地区、日期或案由过滤，日期适用性需在读取原文后另行核验。"]}
