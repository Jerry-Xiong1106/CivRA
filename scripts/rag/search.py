"""Query keyword, vector, or hybrid indexes with source and retrieval diagnostics."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--mode", choices=("keyword", "vector", "exact-vector", "hybrid"), default="exact-vector")
    parser.add_argument("--keyword-index", type=Path, default=Path("data/index/bm25.pkl"))
    parser.add_argument("--vector-index", type=Path, default=Path("data/index/vector_metadata.npz"))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-k", type=int, default=50)
    parser.add_argument("--rrf-k", type=int, default=60)
    args = parser.parse_args()
    if min(args.top_k, args.candidate_k, args.rrf_k) < 1:
        parser.error("top-k, candidate-k and rrf-k must be positive")
    if args.mode == "keyword":
        from backend.knowledge.keyword.bm25_index import load_index
        from backend.knowledge.retrieval.search import search_keyword

        result = search_keyword(args.query, load_index(args.keyword_index), args.top_k)
    else:
        from backend.knowledge.vector.embedding import EmbeddingClient
        from backend.knowledge.vector.vector_index import load_vector_index, search_vector

        index, client = load_vector_index(args.vector_index), EmbeddingClient()
        if args.mode == "hybrid":
            from backend.knowledge.keyword.bm25_index import load_index
            from backend.knowledge.retrieval.hybrid_search import HybridRetriever

            result = HybridRetriever(load_index(args.keyword_index), index, client,
                                     args.candidate_k, args.rrf_k).search(args.query, args.top_k)
        else:
            result = search_vector(args.query, index, client, args.top_k, use_exact=args.mode == "exact-vector")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
