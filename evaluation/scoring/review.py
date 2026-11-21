"""Validate item-level reviews and aggregate separately by setting and task type."""

from collections import defaultdict
from secrets import SystemRandom
from statistics import mean
from uuid import uuid4


def prepare_static_review(records, public_records, gold_records):
    """Mask settings for static, mode-common output judgments."""
    public = {task.task_id: task for task in public_records}
    gold = {task.task_id: task for task in gold_records}
    cases, key = [], {}
    for run in records:
        task = public[run["task_id"]]
        annotation = gold[task.task_id]
        if task.dataset_version != annotation.dataset_version:
            raise ValueError(f"Dataset version mismatch: {task.task_id}")
        if task.task_type != "static":
            continue
        items = [
            item.model_dump(exclude={"applicable_modes"})
            for item in annotation.scoring_items
            if item.dimension != "behavior" and len(set(item.applicable_modes)) == 4
        ]
        if not items:
            continue
        review_id = uuid4().hex
        key[review_id] = run["experiment_run_id"]
        cases.append({
            "review_id": review_id,
            "input": task.to_model_input(),
            "answer": run["output"],
            "rubric": items,
            "annotation_status": annotation.review_status,
        })
    SystemRandom().shuffle(cases)
    return cases, key


def restore_review_ids(reviews, key):
    """Allow ordinary trace reviews alongside masked output reviews."""
    restored = []
    for row in reviews:
        review = dict(row)
        if "review_id" in review:
            review_id = review.pop("review_id")
            if review_id not in key or "experiment_run_id" in review:
                raise ValueError("Ambiguous or unknown masked review ID")
            review["experiment_run_id"] = key[review_id]
        restored.append(review)
    return restored


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
        delivered = run.get('delivery_status', run['status']) == 'completed'
        groups[(run['mode'], run['task_type'])].append({
            'task_id': run['task_id'], 'completed': run['status'] == 'completed',
            'delivered': delivered,
            'task_success': delivered and bool(required) and all(required),
            'dimensions': {name: mean(values) for name, values in dimensions.items()},
        })
    if set(review_map) != used:
        raise ValueError('Review contains an unknown run or inapplicable item')
    return [dict(mode=mode, task_type=kind, tasks=len(rows),
                 completed=sum(r['completed'] for r in rows),
                 delivered=sum(r['delivered'] for r in rows),
                 task_successes=sum(r['task_success'] for r in rows),
                 dimensions={name: mean(r['dimensions'][name] for r in rows if name in r['dimensions'])
                             for name in sorted({n for r in rows for n in r['dimensions']})})
            for (mode, kind), rows in sorted(groups.items())]
