"""BM25 index over article-level provisions: build, persist, and load."""

from __future__ import annotations

import pickle
from dataclasses import dataclass
from pathlib import Path

from rank_bm25 import BM25Okapi

from ..corpus import load_provisions, provision_text
from ..config import DEFAULT_TOP_K, DEFAULT_VARIANT, includes_metadata
from .tokenizer import TOKENIZER_VERSION, tokenize

INDEX_VERSION = "3.0.0"


@dataclass
class BM25Index:
    bm25: BM25Okapi
    provisions: list[dict]
    index_version: str = INDEX_VERSION
    include_metadata: bool = includes_metadata(DEFAULT_VARIANT)
    tokenizer_version: str = TOKENIZER_VERSION

    def search(self, query: str, top_k: int = DEFAULT_TOP_K) -> list[tuple[dict, float]]:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        tokens = tokenize(query)
        if not tokens:
            return []
        scores = self.bm25.get_scores(tokens)
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [(self.provisions[i], float(scores[i])) for i in ranked[:top_k] if scores[i] > 0]


def build_index(jsonl_paths: list[Path], variant: str = DEFAULT_VARIANT) -> BM25Index:
    """Build text or metadata input; preserve the artifact's boolean field."""
    include_metadata = includes_metadata(variant)
    provisions = load_provisions(jsonl_paths)
    corpus = [tokenize(provision_text(p, include_metadata)) for p in provisions]
    return BM25Index(bm25=BM25Okapi(corpus), provisions=provisions,
                     include_metadata=include_metadata)


def save_index(index: BM25Index, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        pickle.dump(index, f)


def load_index(path: Path) -> BM25Index:
    with path.open("rb") as f:
        try:
            index = pickle.load(f)
        except (ModuleNotFoundError, AttributeError):
            raise ValueError("BM25 index uses obsolete module paths; rebuild with scripts/rag/build_bm25_index.py") from None
    if index.index_version != INDEX_VERSION:
        raise ValueError(f"Index version mismatch: {index.index_version} != {INDEX_VERSION}; rebuild the index")
    if index.tokenizer_version != TOKENIZER_VERSION:
        raise ValueError("Tokenizer version mismatch; rebuild the BM25 index")
    return index
