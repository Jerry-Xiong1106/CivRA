"""Keyword retrieval: exact "law + article" lookup backed by BM25 fallback."""

from __future__ import annotations

from pathlib import Path

from .bm25_index import BM25Index, load_index
from .exact_match import match_exact_query

DEFAULT_INDEX_PATH = Path(__file__).resolve().parents[2] / "data" / "index" / "bm25.pkl"


def _to_result(provision: dict, rank: int, score: float, matched_by: list[str]) -> dict:
    return {
        "provision_id": provision["provision_id"],
        "law_title": provision["law_title"],
        "article_no": provision["article_no"],
        "hierarchy_path": provision["hierarchy_path"],
        "text": provision["text"],
        "source": {
            "url": provision["source_url"],
            "file": provision["source_file"],
            "page_start": provision["page_start"],
            "page_end": provision["page_end"],
        },
        "version": {
            "publication_date": provision["publication_date"],
            "effective_date": provision["effective_date"],
            "expiry_date": provision["expiry_date"],
            "status": provision["status"],
        },
        "retrieval": {"rank": rank, "score": score, "matched_by": matched_by},
    }


def search_keyword(query: str, index: BM25Index, top_k: int = 8) -> dict:
    """Keyword-only retrieval: exact law+article lookup first, BM25 fallback.

    Returns the ``search_statutes`` result shape from the roadmap draft, with
    ``status`` one of ``ok`` / ``no_results``.
    """
    if not query or not query.strip():
        return {"status": "no_results", "query": query, "results": [], "warnings": ["empty query"]}

    exact = match_exact_query(query)
    if exact:
        law_title, article_no = exact
        hit = next((p for p in index.provisions if p["law_title"] == law_title and p["article_no"] == article_no), None)
        if hit:
            return {
                "status": "ok",
                "query": query,
                "results": [_to_result(hit, 1, 1.0, ["exact"])],
                "warnings": [],
            }

    ranked = index.search(query, top_k=top_k)
    if not ranked:
        return {"status": "no_results", "query": query, "results": [], "warnings": []}
    results = [_to_result(p, rank, score, ["keyword"]) for rank, (p, score) in enumerate(ranked, 1)]
    return {"status": "ok", "query": query, "results": results, "warnings": []}


def load_default_index() -> BM25Index:
    return load_index(DEFAULT_INDEX_PATH)
