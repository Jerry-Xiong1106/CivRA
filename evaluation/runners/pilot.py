"""Run public tasks only. Private records are never loaded by this module."""

import hashlib
import json
from pathlib import Path
import platform
import subprocess
from datetime import datetime, timezone
import uuid

from backend.knowledge.corpus import load_provisions
from backend.knowledge.vector.vector_index import load_vector_index
from evaluation.baselines.consultation import SYSTEM, run_baseline
from evaluation.schemas.tasks import load_public_tasks


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def assert_current_index(root):
    """Refuse stale vectors when teammates add or modify corpus records."""
    root = Path(root)
    provisions = load_provisions(sorted((root / 'data/processed').glob('*.jsonl')))
    expected = hashlib.sha256(json.dumps(provisions, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    index = load_vector_index(root / 'data/index/vector_metadata.npz')
    if index.config['corpus_sha256'] != expected:
        raise ValueError('Corpus changed: rebuild vector_metadata.npz before evaluating')


def run_pilot(public_path, config, model, retrieval, output_root, root):
    if config.get("paid_execution_enabled") is not True or not config.get("model"):
        raise ValueError("Explicit paid execution and model configuration are required")
    if config["first_execution_modes"] != ["direct_llm", "rag"] or config["repetitions"] != 1:
        raise ValueError("This runner supports the declared two-mode, one-repeat pilot only")
    if config["retrieval"]["rag_queries_per_task"] != 1 or config["retrieval"]["query_source"] != "initial_input":
        raise ValueError("Unsupported retrieval procedure")
    if config["budgets"]["model_retries"] != 0:
        raise ValueError("The baseline model client does not retry")
    assert_current_index(root)
    tasks = load_public_tasks(Path(public_path))
    if any(t.dataset_version != config["dataset_version"] for t in tasks):
        raise ValueError("Task/config dataset version mismatch")
    batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    out = Path(output_root) / batch_id
    out.mkdir(parents=True, exist_ok=False)
    root = Path(root)
    manifest = {
        "batch_id": batch_id, "created_at": datetime.now(timezone.utc).isoformat(),
        "git_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip()),
        "config": config, "public_sha256": sha256(public_path),
        "system_prompt_sha256": hashlib.sha256(SYSTEM.encode()).hexdigest(),
        "corpus_sha256": {p.name: sha256(p) for p in sorted((root / "data/processed").glob("*.jsonl"))},
        "index_sha256": sha256(root / "data/index/vector_metadata.npz"),
        "python": platform.python_version(),
        "implementation_sha256": {str(p.relative_to(root)): sha256(p) for p in sorted((root / "evaluation").rglob("*.py"))},
        "cost": None, "embedding_cost": None,
        "cost_note": "No versioned prices configured; index build excluded from online latency; embedding client does not expose billed usage.",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    records = []
    for task in tasks:
        for mode in config["first_execution_modes"]:
            record = {"experiment_run_id": f"{batch_id}:{task.task_id}:{mode}:1",
                      "task_id": task.task_id, "task_type": task.task_type, "mode": mode, "repetition": 1,
                      **run_baseline(task, mode, model, config, retrieval)}
            records.append(record)
            (out / f"{task.task_id}-{mode}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
            print(f"{task.task_id} {mode}: {record['status']}", flush=True)
    (out / "records.json").write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n")
    summary = {"batch_id": batch_id, "runs": len(records),
               "completed": sum(r["status"] == "completed" for r in records),
               "failed": sum(r["status"] == "failed" for r in records),
               "truncated": sum(r["status"] == "truncated" for r in records),
               "budget_exhausted": sum(r["status"] == "budget_exhausted" for r in records)}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"Results: {out}", flush=True)
    return out
