# Evaluation

Member 5 owns the evaluation protocol, task sets, three baselines, experiment runner, scoring, and result analysis. This directory currently contains a scaffold only; no runners, datasets, or experiment results are implemented.

## Deliverables

- A protocol defining system capabilities, inputs, interaction rules, budgets, and failure handling.
- Approximately 10 pilot tasks, followed by a target of 20 development and 40 final-test tasks. Pilot tasks remain development data.
- Direct LLM, RAG, and Fixed Workflow baselines using shared model and tool interfaces.
- A runner that executes all four settings, including CivRA Agent, and saves outputs, traces, usage, latency, costs, and failures.
- Task-specific scoring checklists, blinded cross-review records, comparisons, and failure analysis.
- Versioned configurations and instructions sufficient to repeat an experiment.

## Layout

| Path | Contents |
| --- | --- |
| `schemas/` | Task, output, run-record, and score formats |
| `configs/` | Model, setting, budget, and experiment configurations |
| `datasets/pilot/` | Shareable development inputs and examples |
| `baselines/` | Direct LLM, RAG, and Fixed Workflow implementations |
| `adapters/` | Integration with shared model, retrieval, business-tool, and Agent interfaces |
| `mocks/` | Deterministic responses and tools for offline development |
| `runners/` | Batch execution, interaction control, persistence, and failure recording |
| `scoring/` | Score processing, metric aggregation, and analysis |
| [`../docs/evaluation/`](../docs/evaluation/) | Protocol, annotation guide, and rubric |
| [`../tests/evaluation/`](../tests/evaluation/) | Evaluation-specific checks |

## Work sequence

1. Draft task formats, scoring items, and the protocol.
2. Build and review the pilot tasks.
3. Use mocks to check loading, disclosure rules, failure handling, and result aggregation.
4. Integrate the shared interfaces and implement the three baselines.
5. Run the pilot against all four settings and resolve protocol or annotation problems.
6. Freeze the final-test configuration, run the experiments, and complete cross-review.
7. Report quality, resource consumption, robustness, and failure cases.

Mock runs check the evaluation software; they are not evidence of model performance. Execution commands will be documented when runners are available.

## Integration responsibilities

| Owner | Required contribution |
| --- | --- |
| Member 2 | Retrieval interface, source references, knowledge-base versions, and retrieval annotations |
| Member 3 | Business-tool interfaces and annotations for facts, evidence, calculations, and documents |
| Member 4 | Shared model interface, complete Agent, run interface, and trace export |
| Member 5 | Baselines, protocol, task formats, runner, scoring, and analysis |
| All members | Cross-review and resolution of annotation disagreements |

Keep provisional adapters local to this module. Document proposed cross-module contracts before adopting shared definitions.

## Data handling

Only shareable development material belongs in `datasets/pilot/`. Keep final-test gold annotations and hidden facts in controlled storage, separate from prompts and searchable data. Disclose hidden facts only under the protocol.

Do not commit credentials, identifiable personal data, or materials without confirmed publication rights. Before generating local results or controlled data, configure their storage and Git ignore rules. Record code, dataset, prompt, model, configuration, and knowledge-base versions for every experiment.

## Documents

- [Protocol](../docs/evaluation/protocol.md)
- [Annotation guide](../docs/evaluation/annotation_guide.md)
- [Scoring rubric](../docs/evaluation/rubric.md)
- [Interface requirements (draft)](../docs/evaluation/interface_requirements.md)
