"""Plot delivered answers and provisional content outcomes without token scoring."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.evaluation.compare_evaluation_pilot import LABELS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch-dir', type=Path, required=True)
    args = parser.parse_args()
    import matplotlib.pyplot as plt
    batch = args.batch_dir
    records = json.loads((batch / 'records.json').read_text())
    scores = json.loads((batch / 'score_summary.json').read_text())['groups']
    audit = json.loads((batch / 'quality_audit.json').read_text())
    summary = {}
    for mode in LABELS:
        rows = [r for r in records if r['mode'] == mode]
        checked = [r for r in audit if r['mode'] == mode]
        if {r['task_id'] for r in rows} != {r['task_id'] for r in checked}:
            raise ValueError('Quality audit must cover each run')
        summary[mode] = {
            'attempted': len(rows),
            'delivered': sum(r['delivery_status'] == 'completed' for r in rows),
            'required_items_passed': sum(r['task_successes'] for r in scores if r['mode'] == mode),
            'citation_issue_flagged': sum(r['citation_issue'] for r in checked),
            'citation_audited': len(checked),
            'support_issues': sum(r['issue_kind'] == 'support_or_wording' for r in checked),
            'source_link_issues': sum(r['issue_kind'] == 'source_link' for r in checked),
            'review_kind': 'assistant_provisional',
        }
    (batch / 'quality_metrics.json').write_text(json.dumps(summary, indent=2)+'\n')
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.1), layout='constrained')
    for x, mode in enumerate(LABELS):
        values = summary[mode]
        for offset, name, color in [(-0.18, 'delivered', '#075985'), (0.18, 'required_items_passed', '#9a3412')]:
            v = values[name]
            axes[0].bar(x+offset, v, width=0.34, color=color,
                        label={'delivered':'Delivered answer','required_items_passed':'Required content items passed'}[name] if x==0 else None)
            axes[0].text(x+offset, v+0.12, str(v), ha='center', fontsize=10)
        v=values['citation_issue_flagged']
        axes[1].bar(x, values['support_issues'], color='#854d0e',width=0.5,
                     label='Source support / wording' if x==0 else None)
        axes[1].bar(x, values['source_link_issues'], bottom=values['support_issues'], color='#d6d3d1',
                     edgecolor='#57534e', hatch='///',width=0.5,
                     label='Missing claim-to-source link' if x==0 else None)
        axes[1].text(x, v+0.12, str(v), ha='center',fontsize=10)
    count=max(r['attempted'] for r in summary.values())
    for axis in axes:
        axis.set(xticks=range(4), xticklabels=['Direct LLM','RAG','Fixed\nWorkflow','Agent'],
                 ylim=(0, count+3.8), yticks=range(0,count+1,2), ylabel=f'Tasks (out of {count})')
        axis.spines[['top','right']].set_visible(False)
        axis.set_axisbelow(True)
        axis.grid(axis='y',alpha=.2)
    axes[0].set_title('Delivery and content')
    axes[0].legend(loc='upper left',frameon=False,fontsize=8)
    axes[1].set_title('Citation diagnostics')
    axes[1].legend(loc='upper left',frameon=False,fontsize=8)
    fig.suptitle('DeepSeek development pilot · no cumulative token cap\nSingle run per task · provisional assistant review',fontsize=12)
    fig.savefig(batch/'quality_comparison.png',dpi=220,facecolor='white')
    plt.close(fig)
    print(json.dumps(summary,indent=2))


if __name__ == '__main__':
    main()
