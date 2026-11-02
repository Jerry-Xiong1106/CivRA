"""BM25 index over article-level provisions: build, persist, and load."""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from pathlib import Path

from rank_bm25 import BM25Okapi

from .tokenizer import tokenize

INDEX_VERSION = "1.0.0"
PROVISION_FIELDS = (
    "provision_id", "law_id", "law_title", "article_no", "hierarchy_path",
    "text", "source_file", "source_url", "page_start", "page_end",
    "publication_date", "effective_date", "expiry_date", "status",
)


@dataclass
class BM25Index:
    bm25: BM25Okapi
    provisions: list[dict]
    index_version: str = INDEX_VERSION

    def search(self, query: str, top_k: int = 8) -> list[tuple[dict, float]]:
        tokens = tokenize(query)
        if not tokens:
            return []
        scores = self.bm25.get_scores(tokens)
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [(self.provisions[i], float(scores[i])) for i in ranked[:top_k] if scores[i] > 0]


def load_provisions(jsonl_paths: list[Path]) -> list[dict]:
    provisions = []
    for path in jsonl_paths:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if line.strip():
                record = json.loads(line)
                provisions.append({field: record.get(field) for field in PROVISION_FIELDS})
    return provisions


def build_index(jsonl_paths: list[Path], include_metadata: bool = False) -> BM25Index:
    """Build a BM25 index over provision text. ``include_metadata`` adds law
    title and hierarchy path to the indexed tokens; left off by default so the
    text-only baseline can be compared against it during evaluation."""
    provisions = load_provisions(jsonl_paths)
    corpus = []
    for p in provisions:
        content = f"{p['law_title']} {' '.join(p['hierarchy_path'] or [])} {p['text']}" if include_metadata else p["text"]
        corpus.append(tokenize(content))
    return BM25Index(bm25=BM25Okapi(corpus), provisions=provisions)


def save_index(index: BM25Index, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        pickle.dump(index, f)


def load_index(path: Path) -> BM25Index:
    with path.open("rb") as f:
        index = pickle.load(f)
    if index.index_version != INDEX_VERSION:
        raise ValueError(f"Index version mismatch: {index.index_version} != {INDEX_VERSION}; rebuild the index")
    return index
