"""Common result schema and input checks, without retrieval channel imports."""
import unicodedata


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
