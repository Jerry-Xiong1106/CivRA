"""Validate pilot data offline; no model, retrieval, or API credentials needed."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from evaluation.schemas.tasks import load_public_tasks, validate_bundle  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", type=Path, default=ROOT / "evaluation/datasets/pilot/tasks.jsonl")
    parser.add_argument("--private-dir", type=Path, help="Optional controller/scorer-only annotation directory")
    args = parser.parse_args()
    try:
        if args.private_dir:
            result = validate_bundle(args.public, args.private_dir / "hidden.jsonl", args.private_dir / "gold.jsonl")
        else:
            result = {"tasks": len(load_public_tasks(args.public)), "scope": "public_only"}
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Validation failed: {exc}\n")
    result["public_sha256"] = hashlib.sha256(args.public.read_bytes()).hexdigest()
    result["status"] = "valid"
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
