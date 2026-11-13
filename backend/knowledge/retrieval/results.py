"""Common result schema and input checks, without retrieval channel imports."""
import unicodedata
import hashlib


def no_results(query: str, reason: str, message: str) -> dict:
    return {"status": "no_results", "query": query, "results": [],
            "reason": reason, "warnings": [message]}


def query_preflight(query: str) -> dict | None:
    """Reject blank or punctuation-only input without changing query text."""
    if not query or not query.strip():
        return no_results(query, "empty_query", "查询为空，请输入检索内容。")
    if not any(unicodedata.category(char)[0] in "LN" for char in query):
        return no_results(query, "no_searchable_content", "查询不含文字或数字，请输入检索内容。")
    return None


def to_result(provision: dict, rank: int, score: float, matched_by: list[str]) -> dict:
    return {
        "provision_id": provision["provision_id"], "law_title": provision["law_title"],
        "article_no": provision["article_no"], "hierarchy_path": provision["hierarchy_path"],
        "text": provision["text"],
        "source": {"url": provision["source_url"], "file": provision["source_file"],
                   "page_start": provision["page_start"], "page_end": provision["page_end"]},
        "version": {"publication_date": provision["publication_date"],
                    "effective_date": provision["effective_date"],
                    "expiry_date": provision["expiry_date"], "status": provision["status"]},
        "retrieval": {"rank": rank, "score": score, "matched_by": matched_by},
    }


def to_search_match(result: dict, source_metadata: dict) -> dict:
    """Convert an existing hit; preserve text, order, score and diagnostics."""
    version, source = result["version"], result["source"]
    content_hash = hashlib.sha256(result["text"].encode("utf-8")).hexdigest()
    source_version = version["publication_date"] or f"sha256:{content_hash}"
    registered = source_metadata.get((result["law_title"], version["publication_date"], source["file"]), {})
    ref = {
        "source_id": result["provision_id"], "source_type": "statute", "title": result["law_title"],
        "locator": {"article": result["article_no"], "page_start": source["page_start"], "page_end": source["page_end"]},
        "source_version": source_version,
        "effective_from": version["effective_date"], "effective_to": version["expiry_date"],
        "source_file": source["file"], "url": source["url"], "content_sha256": content_hash,
        "retrieved_at": None, "retrieved_date": registered.get("retrieved_at"),
    }
    retrieval = result["retrieval"]
    score_type = retrieval.get("score_type") or {"keyword": "bm25", "vector": "cosine"}.get(retrieval["matched_by"][0])
    diagnostics = {key: value for key, value in retrieval.items() if key not in ("rank", "score", "score_type")}
    return {"text": result["text"], "source_ref": ref,
            "rank": retrieval["rank"], "score": retrieval["score"], "score_type": score_type,
            "retrieval": diagnostics, "hierarchy_path": result["hierarchy_path"]}
