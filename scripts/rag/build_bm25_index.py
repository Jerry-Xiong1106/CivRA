"""Build a BM25 index from article-level JSONL and persist it to disk."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.knowledge.keyword.bm25_index import build_index, save_index  # noqa: E402
from backend.knowledge.config import CORPUS_DIR, DEFAULT_VARIANT, INPUT_VARIANTS, keyword_index_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("jsonl", type=Path, nargs="*", default=[CORPUS_DIR])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--variant", choices=INPUT_VARIANTS, default=DEFAULT_VARIANT)
    args = parser.parse_args()

    paths = []
    for jsonl in args.jsonl:
        paths.extend(sorted(jsonl.glob("*.jsonl")) if jsonl.is_dir() else [jsonl])

    index = build_index(paths, variant=args.variant)
    output = args.output or keyword_index_path(args.variant)
    save_index(index, output)
    print(json.dumps({
        "provisions": len(index.provisions),
        "include_metadata": index.include_metadata,
        "tokenizer_version": index.tokenizer_version,
        "output": str(output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
