"""Build a BM25 index from article-level JSONL and persist it to disk."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.knowledge.bm25_index import build_index, save_index  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("jsonl", type=Path, nargs="+", default=[Path("data/processed")])
    parser.add_argument("--output", type=Path, default=Path("data/index/bm25.pkl"))
    parser.add_argument("--include-metadata", action="store_true", help="Index law title and hierarchy path alongside article text")
    args = parser.parse_args()

    paths = []
    for jsonl in args.jsonl:
        paths.extend(sorted(jsonl.glob("*.jsonl")) if jsonl.is_dir() else [jsonl])

    index = build_index(paths, include_metadata=args.include_metadata)
    save_index(index, args.output)
    print(json.dumps({
        "provisions": len(index.provisions),
        "include_metadata": args.include_metadata,
        "output": str(args.output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
