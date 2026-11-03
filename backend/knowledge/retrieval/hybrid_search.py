"""BM25 + dense retrieval with reciprocal rank fusion and exact-article routing."""
from .search import _to_result, exact_result


def reciprocal_rank_fusion(channels, top_k=8, rrf_k=60):
    """Fuse ranked (provision, raw_score) lists; count each ID once per channel."""
    if top_k < 1 or rrf_k < 1:
        raise ValueError("top_k and rrf_k must be positive")
    candidates = {}
    for channel, ranked in channels.items():
        seen = set()
        for rank, (provision, raw_score) in enumerate(ranked, 1):
            pid = provision["provision_id"]
            if pid in seen:
                continue
            seen.add(pid)
            item = candidates.setdefault(pid, {"provision": provision, "score": 0.0, "channels": {}})
            contribution = 1.0 / (rrf_k + rank)
            item["score"] += contribution
            item["channels"][channel] = {"rank": rank, "score": float(raw_score), "rrf_contribution": contribution}
    # Explicit tie-breaking keeps output independent of dictionary insertion order.
    ranked = sorted(candidates.values(), key=lambda item: (
        -item["score"], min(c["rank"] for c in item["channels"].values()), item["provision"]["provision_id"],
    ))
    results = []
    for rank, item in enumerate(ranked[:top_k], 1):
        result = _to_result(item["provision"], rank, item["score"], sorted(item["channels"]))
        result["retrieval"].update({"score_type": "rrf", "rrf_k": rrf_k, "channels": item["channels"]})
        results.append(result)
    return results


class HybridRetriever:
    def __init__(self, keyword_index, vector_index, client, candidate_k=50, rrf_k=60):
        if candidate_k < 1 or rrf_k < 1:
            raise ValueError("candidate_k and rrf_k must be positive")
        keyword = {p["provision_id"]: p for p in keyword_index.provisions}
        dense = {p["provision_id"]: p for p in vector_index.provisions}
        if len(keyword) != len(keyword_index.provisions) or len(dense) != len(vector_index.provisions) or keyword != dense:
            raise ValueError("BM25 and vector indexes must contain identical provisions and versions; rebuild indexes")
        if (client.model, client.base_url) != (vector_index.config["model"], vector_index.config["base_url"]):
            raise ValueError("Query embedding model/endpoint differs from the vector index")
        self.keyword_index = keyword_index
        self.vector_index = vector_index
        self.client = client
        self.candidate_k = candidate_k
        self.rrf_k = rrf_k

    def search(self, query, top_k=8, use_exact=True, query_vector=None):
        """An optional precomputed query vector allows evaluation without extra API calls."""
        if top_k < 1:
            raise ValueError("top_k must be positive")
        if not query or not query.strip():
            return {"status": "no_results", "query": query, "results": [], "warnings": ["empty query"]}
        if use_exact:
            hit = exact_result(query, self.keyword_index.provisions)
            if hit:
                return {"status": "ok", "query": query, "results": [hit], "warnings": []}
        if query_vector is None:
            query_vector = self.client.embed([query])[0]
        depth = max(top_k, self.candidate_k)
        channels = {
            "keyword": self.keyword_index.search(query, depth),
            "vector": self.vector_index.search_vector(query_vector, depth),
        }
        results = reciprocal_rank_fusion(channels, top_k, self.rrf_k)
        for result in results:
            result["retrieval"]["candidate_k"] = depth
        return {"status": "ok" if results else "no_results", "query": query, "results": results, "warnings": []}
