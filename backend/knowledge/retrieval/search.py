"""Keyword retrieval: exact "law + article" lookup backed by BM25 fallback."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .exact_match import match_exact_query

if TYPE_CHECKING:
    from ..keyword.bm25_index import BM25Index


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


def exact_result(query: str, provisions: list[dict]) -> dict | None:
    """Shared law+article route for keyword, vector, and hybrid retrieval."""
    target = match_exact_query(query)
    if target:
        provision = next((p for p in provisions if (p["law_title"], p["article_no"]) == target), None)
        if provision:
            result = _to_result(provision, 1, 1.0, ["exact"])
            result["retrieval"].update({"score_type": "exact", "channels": {"exact": {"rank": 1, "score": 1.0}}})
            return result
    return None


def search_keyword(query: str, index: BM25Index, top_k: int = 8) -> dict:
    """Keyword-only retrieval: exact law+article lookup first, BM25 fallback.

    Returns the ``search_statutes`` result shape from the roadmap draft, with
    ``status`` one of ``ok`` / ``no_results``.
    """
    if top_k < 1:
        raise ValueError("top_k must be positive")
    if not query or not query.strip():
        return {"status": "no_results", "query": query, "results": [], "warnings": ["empty query"]}

    hit = exact_result(query, index.provisions)
    if hit:
        return {"status": "ok", "query": query, "results": [hit], "warnings": []}

    ranked = index.search(query, top_k=top_k)
    if not ranked:
        return {"status": "no_results", "query": query, "results": [], "warnings": []}
    results = [_to_result(p, rank, score, ["keyword"]) for rank, (p, score) in enumerate(ranked, 1)]
    return {"status": "ok", "query": query, "results": results, "warnings": []}
