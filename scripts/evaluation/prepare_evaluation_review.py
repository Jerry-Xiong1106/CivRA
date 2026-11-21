"""Prepare a local static-answer review packet with mode labels masked."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from evaluation.schemas.tasks import GoldTask, load_public_tasks, read_records  # noqa: E402
from evaluation.scoring.review import prepare_static_review  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-dir", type=Path, required=True)
    parser.add_argument("--public", type=Path, default=ROOT / "evaluation/datasets/pilot/tasks.jsonl")
    parser.add_argument("--gold", type=Path, required=True)
    args = parser.parse_args()
    packet_path = args.batch_dir / "static_review_packet.json"
    key_path = args.batch_dir / "static_review_key.json"
    if packet_path.exists() or key_path.exists():
        parser.error("Review packet or key already exists for this batch")
    manifest = json.loads((args.batch_dir / "manifest.json").read_text())
    if hashlib.sha256(args.public.read_bytes()).hexdigest() != manifest["public_sha256"]:
        parser.error("Public tasks do not match the batch")
    records = json.loads((args.batch_dir / "records.json").read_text())
    packet, key = prepare_static_review(records, load_public_tasks(args.public), read_records(args.gold, GoldTask))
    if not packet:
        parser.error("No static review items common to all settings")
    key_path.write_text(json.dumps(key, indent=2) + "\n", encoding="utf-8")
    packet_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared {len(packet)} answers for review: {packet_path}")


if __name__ == "__main__":
    main()
