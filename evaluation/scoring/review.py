"""Validate item-level reviews and aggregate separately by setting and task type."""

from collections import defaultdict
from statistics import mean


def summarize_reviews(records, gold_records, reviews):
    gold = {g.task_id: g for g in gold_records}
    review_map = {}
    for review in reviews:
        key = (review['experiment_run_id'], review['item_id'])
        if key in review_map:
            raise ValueError('Duplicate review')
        if type(review['score']) is not int or review['score'] not in (0, 1, 2):
            raise ValueError('Review score must be an integer from 0 to 2')
        if not review.get('reason') or not review.get('reviewer') or not review.get('review_kind'):
            raise ValueError('Review must identify evidence, reviewer, and review kind')
        review_map[key] = review
    groups, used = defaultdict(list), set()
    for run in records:
        task_gold = gold[run['task_id']]
        items = [i for i in task_gold.scoring_items if run['mode'] in i.applicable_modes]
        required, dimensions = [], defaultdict(list)
        for item in items:
            key = (run['experiment_run_id'], item.item_id)
            if key not in review_map:
                raise ValueError(f'Missing review: {key}')
            used.add(key)
            score = review_map[key]['score']
            dimensions[item.dimension].append(score)
            if item.required_for_success:
                required.append(score == 2)
        groups[(run['mode'], run['task_type'])].append({
            'task_id': run['task_id'], 'completed': run['status'] == 'completed',
            'task_success': bool(required) and all(required),
            'dimensions': {name: mean(values) for name, values in dimensions.items()},
        })
    if set(review_map) != used:
        raise ValueError('Review contains an unknown run or inapplicable item')
    return [dict(mode=mode, task_type=kind, tasks=len(rows),
                 completed=sum(r['completed'] for r in rows),
                 task_successes=sum(r['task_success'] for r in rows),
                 dimensions={name: mean(r['dimensions'][name] for r in rows if name in r['dimensions'])
                             for name in sorted({n for r in rows for n in r['dimensions']})})
            for (mode, kind), rows in sorted(groups.items())]
