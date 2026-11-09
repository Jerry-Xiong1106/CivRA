"""Aggregate explicit reviews; does not generate or infer legal scores."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.schemas.tasks import GoldTask, read_records  # noqa: E402
from evaluation.scoring.review import summarize_reviews  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch-dir', type=Path, required=True)
    parser.add_argument('--gold', type=Path, required=True)
    parser.add_argument('--reviews', type=Path, required=True)
    args = parser.parse_args()
    records = json.loads((args.batch_dir / 'records.json').read_text())
    reviews = json.loads(args.reviews.read_text())
    gold = read_records(args.gold, GoldTask)
    summary = {'annotation_status': sorted({g.review_status for g in gold}),
               'review_kinds': sorted({r['review_kind'] for r in reviews}),
               'groups': summarize_reviews(records, gold, reviews)}
    (args.batch_dir / 'score_summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
