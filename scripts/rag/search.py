"""Query keyword, vector, or hybrid indexes with source and retrieval diagnostics."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.knowledge.config import (DEFAULT_MODE, DEFAULT_VARIANT, DEFAULT_TOP_K,
    DEFAULT_CANDIDATE_K, DEFAULT_RRF_K, MODES, INPUT_VARIANTS, RetrievalConfig)
from backend.knowledge.retrieval.service import RetrievalService
from backend.knowledge.retrieval.results import query_preflight


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--mode", choices=MODES, default=DEFAULT_MODE)
    parser.add_argument("--variant", choices=INPUT_VARIANTS, default=DEFAULT_VARIANT,
                        help="Document input; in hybrid mode selects vector input, BM25 stays metadata")
    parser.add_argument("--keyword-index", type=Path)
    parser.add_argument("--vector-index", type=Path)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--candidate-k", type=int, default=DEFAULT_CANDIDATE_K)
    parser.add_argument("--rrf-k", type=int, default=DEFAULT_RRF_K)
    args = parser.parse_args()
    try:
        config = RetrievalConfig.from_mode(args.mode, variant=args.variant, top_k=args.top_k,
                                           candidate_k=args.candidate_k, rrf_k=args.rrf_k)
    except ValueError as exc:
        parser.error(str(exc))

    result = query_preflight(args.query)
    if result is not None:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    service = RetrievalService.from_config(config, keyword_path=args.keyword_index,
                                           vector_path=args.vector_index)
    result = service.search(args.query)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
