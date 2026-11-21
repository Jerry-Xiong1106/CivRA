"""Summarize saved pilot runs and optionally plot observed delivery and latency."""

import argparse
import json
from pathlib import Path
from statistics import mean
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from evaluation.runners.pilot import token_budget_audit  # noqa: E402

LABELS = {"direct_llm": "Direct LLM", "rag": "RAG",
          "fixed_workflow": "Fixed Workflow", "agent": "CivRA Agent"}


def compare(records, token_limit):
    summary = {}
    for mode in LABELS:
        rows = [row for row in records if row["mode"] == mode]
        if not rows:
            continue
        delivered = [row for row in rows if row["delivery_status"] == "completed"]
        audits = [token_budget_audit(row, token_limit) for row in rows]
        tokens = [audit["total_tokens"] for audit in audits if audit["total_tokens"] is not None]
        seconds = [row["duration_ms"] / 1000 for row in rows if row["duration_ms"] is not None]
        summary[mode] = {
            "runs": len(rows),
            "runtime_completed": sum(row["status"] == "completed" for row in rows),
            "delivered": len(delivered),
            "delivered_within_token_budget": sum(
                row["delivery_status"] == "completed" and audit["status"] == "within_limit"
                for row, audit in zip(rows, audits)
            ),
            "token_overruns": sum(audit["status"] == "exceeded" for audit in audits),
            "token_usage_coverage": len(tokens),
            "observed_model_tokens": sum(tokens) if tokens else None,
            "mean_duration_seconds": round(mean(seconds), 3) if seconds else None,
            "duration_coverage": len(seconds),
        }
        if mode in {"rag", "fixed_workflow"}:
            summary[mode]["answers_with_explicit_bindings"] = sum(
                bool(row.get("citation_check", {}).get("bindings")) for row in rows
            )
    return summary


def plot_comparison(records, summary, batch_id, token_limit, path):
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    fig, (delivery, latency) = plt.subplots(1, 2, figsize=(10, 3.8), layout="constrained", sharey=True)
    if token_limit is None:
        raise ValueError("Use plot_quality_pilot.py for runs without a cumulative token cap")
    modes = list(summary)
    positions = list(range(len(modes)))
    modes_data = list(summary.values())
    delivered = [mode["delivered"] for mode in modes_data]
    compliant = [mode["delivered_within_token_budget"] for mode in modes_data]
    blue = "#075985"
    delivery.barh(positions, delivered, color="#dbedf6", height=0.57, label="Delivered")
    delivery.barh(positions, compliant, color=blue, height=0.30, label="Within token budget")
    for pos, counts in enumerate(modes_data):
        delivery.text(counts["runs"] + 0.12, pos,
                      f'{counts["delivered_within_token_budget"]} / {counts["delivered"]} / {counts["runs"]}',
                      va="center", fontsize=9)
    max_runs = max(mode["runs"] for mode in modes_data)
    delivery.set(title="Answer delivery", xlabel="Runs (within limit / delivered / attempted)",
                 xlim=(0, max_runs * 1.29), yticks=positions, yticklabels=[LABELS[mode] for mode in modes])
    delivery.set_xticks(range(0, max_runs + 1, max(1, max_runs // 5)))
    delivery.invert_yaxis()

    for pos, mode in enumerate(modes):
        durations = [row["duration_ms"] / 1000 for row in records
                     if row["mode"] == mode and row["duration_ms"] is not None]
        offsets = [(rank % 5 - 2) * 0.045 for rank in range(len(durations))]
        latency.scatter(durations, [pos + offset for offset in offsets], color=blue, alpha=0.75, s=26)
        if durations:
            latency.scatter(mean(durations), pos, color="#9a3412", marker="D", s=37, zorder=3)
    latency.set(xlabel="Seconds per task", xlim=(0, None))
    latency.set_title("Online duration", loc="left")
    latency.legend(handles=[Line2D([], [], marker="o", linestyle="", color=blue, label="One run"),
                            Line2D([], [], marker="D", linestyle="", color="#9a3412", label="Mean")],
                   loc="lower right", bbox_to_anchor=(1, 1.01), ncol=2, frameon=False, fontsize=8)
    for axis in (delivery, latency):
        axis.spines[["top", "right"]].set_visible(False)
        axis.set_axisbelow(True)
        axis.grid(axis="x", color="#dbe3eb", linewidth=0.65)
    fig.suptitle(f"Consultation development pilot · {batch_id}\nCumulative limit: {token_limit:,} model tokens",
                 fontsize=12, fontweight="bold")
    fig.savefig(path, dpi=220, facecolor="white",
                metadata={"Description": f"Source: {batch_id}/records.json. Latency excludes missing durations."})
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-dir", type=Path, required=True)
    parser.add_argument("--chart", action="store_true", help="Write comparison.png (requires Matplotlib)")
    args = parser.parse_args()
    manifest = json.loads((args.batch_dir / "manifest.json").read_text())
    records = json.loads((args.batch_dir / "records.json").read_text())
    limit = manifest["config"]["budgets"]["max_total_tokens"]
    metrics = compare(records, limit)
    if not metrics:
        parser.error("Batch contains no recognized settings")
    output = json.dumps({"batch_id": manifest["batch_id"], "token_limit": limit, "modes": metrics}, indent=2)
    (args.batch_dir / "comparison_metrics.json").write_text(output + "\n", encoding="utf-8")
    if args.chart:
        plot_comparison(records, metrics, manifest["batch_id"], limit, args.batch_dir / "comparison.png")
    print(output)


if __name__ == "__main__":
    main()
