"""Aggregate explicit reviews; does not generate or infer legal scores."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from evaluation.schemas.tasks import GoldTask, read_records  # noqa: E402
from evaluation.scoring.review import restore_review_ids, summarize_reviews  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch-dir', type=Path, required=True)
    parser.add_argument('--gold', type=Path, required=True)
    parser.add_argument('--reviews', type=Path, required=True)
    parser.add_argument('--review-key', type=Path, help='Coordinator-only mapping for masked review IDs')
    parser.add_argument('--output', type=Path, help='Optional separate path for the score summary')
    args = parser.parse_args()
    records = json.loads((args.batch_dir / 'records.json').read_text())
    reviews = json.loads(args.reviews.read_text())
    if args.review_key:
        reviews = restore_review_ids(reviews, json.loads(args.review_key.read_text()))
    gold = read_records(args.gold, GoldTask)
    manifest_path = args.batch_dir / 'manifest.json'
    if manifest_path.is_file():
        dataset_version = json.loads(manifest_path.read_text())['config']['dataset_version']
        if any(task.dataset_version != dataset_version for task in gold):
            parser.error('Gold annotations do not match the batch dataset version')
    summary = {'annotation_status': sorted({g.review_status for g in gold}),
               'review_kinds': sorted({r['review_kind'] for r in reviews}),
               'groups': summarize_reviews(records, gold, reviews)}
    output = args.output or args.batch_dir / 'score_summary.json'
    output.write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
