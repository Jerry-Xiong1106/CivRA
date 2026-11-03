"""BM25 + dense candidate fusion; routing is owned by RetrievalService."""
from ..config import DEFAULT_TOP_K, DEFAULT_CANDIDATE_K, DEFAULT_RRF_K
from .results import to_result


def reciprocal_rank_fusion(channels, top_k=DEFAULT_TOP_K, rrf_k=DEFAULT_RRF_K):
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
        result = to_result(item["provision"], rank, item["score"], sorted(item["channels"]))
        result["retrieval"].update({"score_type": "rrf", "rrf_k": rrf_k, "channels": item["channels"]})
        results.append(result)
    return results


def search_hybrid(query, keyword_index, vector_index, query_vector, top_k=DEFAULT_TOP_K,
                  candidate_k=DEFAULT_CANDIDATE_K, rrf_k=DEFAULT_RRF_K):
    depth = max(top_k, candidate_k)
    channels = {"keyword": keyword_index.search(query, depth),
                "vector": vector_index.search_vector(query_vector, depth)}
    results = reciprocal_rank_fusion(channels, top_k, rrf_k)
    for result in results:
        result["retrieval"]["candidate_k"] = depth
    return results
