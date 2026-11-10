"""BM25 index over article-level provisions: build, persist, and load."""

from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from pathlib import Path

from rank_bm25 import BM25Okapi

from ..corpus import load_provisions, provision_document
from ..config import DEFAULT_VARIANT, includes_metadata
from .tokenizer import TOKENIZER_VERSION, get_tokenizer_fingerprint, tokenize

INDEX_VERSION = "3.0.0"


@dataclass
class BM25Index:
    bm25: BM25Okapi
    provisions: list[dict]
    index_version: str = INDEX_VERSION
    include_metadata: bool = includes_metadata(DEFAULT_VARIANT)
    tokenizer_version: str = TOKENIZER_VERSION
    tokenizer_fingerprint: str = field(default_factory=get_tokenizer_fingerprint)


def build_index(jsonl_paths: list[Path], variant: str = DEFAULT_VARIANT) -> BM25Index:
    """Build text or metadata input; preserve the artifact's boolean field."""
    from .bm25_retriever import ScoredBM25Retriever

    fingerprint = get_tokenizer_fingerprint()
    include_metadata = includes_metadata(variant)
    provisions = load_provisions(jsonl_paths)
    retriever = ScoredBM25Retriever.from_documents(
        [provision_document(p, include_metadata) for p in provisions], preprocess_func=tokenize)
    if get_tokenizer_fingerprint() != fingerprint:
        raise ValueError("Tokenizer configuration changed during index construction; rebuild the BM25 index")
    return BM25Index(bm25=retriever.vectorizer, provisions=provisions,
                     include_metadata=include_metadata, tokenizer_fingerprint=fingerprint)


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
    fingerprint = getattr(index, "tokenizer_fingerprint", None)
    if fingerprint is None:
        raise ValueError("BM25 index has no tokenizer fingerprint; rebuild with scripts/rag/build_bm25_index.py")
    if fingerprint != get_tokenizer_fingerprint():
        raise ValueError("Tokenizer fingerprint mismatch (dictionary, rules or jieba version changed); rebuild with scripts/rag/build_bm25_index.py")
    return index
