# Scoring rubric

Status: Draft v0.1. Owner: Member 5.

## Scoring rules

Score actual outputs against task-specific checklists. Accept supported alternatives rather than exact reference wording. Freeze the checklists and aggregation rules before final testing.

| Score | Meaning |
| --- | --- |
| 0 | Incorrect, missing, or materially inconsistent with evidence or authorities |
| 1 | Partially satisfied, with important omissions or incomplete explanation |
| 2 | Correct and sufficient for the item |
| N/A | Not required under the task's predefined applicability rules |

Required content missing because of a system failure scores zero; it is not N/A. Report quality dimensions, behavior, and resource measurements separately rather than combining them into an undefined overall score.

## Quality dimensions

| Dimension | Checks |
| --- | --- |
| Legal and task outcomes | Issue identification, applicable authorities and versions, factual premises, user-goal coverage, and uncertainty |
| Facts and evidence | Fact extraction, source linkage, missing information, conflicts, and updates where applicable |
| Citation reliability | Source existence, locator accuracy, support for the associated claim, and version applicability |
| Guidance and documents | Selected-route consistency, required steps and materials, field and amount consistency, and explicit missing fields |

A valid link or provision name alone does not establish citation support. If authority is required but absent, score the corresponding missing item. Do not require documents or procedural outputs that the task did not request.

Determine task success separately using the task's required conditions. A high average score does not override a failed required condition.

## Example items

| Item | 0 | 1 | 2 |
| --- | --- | --- | --- |
| Identify a documented partial repayment | Omitted or denied | Mentioned without its amount or effect | Supported amount identified and reflected in the balance |
| Handle conflicting amounts | Conflict ignored or resolved without support | Conflict noted without the discrepancy or unresolved facts | Discrepancy located and verification needs or an effective question provided |
| Identify a missing fact | Fact invented or gap ignored | General statement that information is missing | Specific gap identified with its impact and a targeted request or conditional conclusion |

Adapt examples to each task; they are not universal legal-answer templates.

## Behavior review

Use execution traces to assess tool choice, arguments, ordering, clarification quality, and error recovery. Define expected behavior from the task, not from one preferred trajectory; multiple valid action sequences may exist.

For settings without a capability, mark the corresponding behavior item N/A while scoring output quality normally. If missing logs prevent judgment, mark the item unevaluable and report trace coverage. Do not infer tool correctness from a fluent answer.

## Programmatic measurements

- Execution completion rate and failure types.
- Model and tool calls, including recorded retries.
- Available input/output token usage and its coverage.
- End-to-end latency under the protocol's timing boundaries.
- Billed or estimated cost, including currency and price-table version.

Distinguish an empty retrieval result from a tool error. Unknown usage or cost is missing data, not zero. Report measurement coverage alongside averages. Follow the frozen rules for caching, retries, and user-wait time.

## Aggregation

For each quality dimension, first average applicable item scores within each task, then average across applicable tasks. Report task and item counts. Exclude N/A items from denominators. This prevents tasks with more checklist items from receiving unintended extra weight.

System failures stay in execution-completion denominators. Score observable partial output normally and missing required items as zero. Report task success separately from technical execution completion.

When using repeated runs, report the declared number of attempts and aggregate repetitions within each task before task-level summaries. Preserve variability rather than selecting the best run.

Report static and interactive results separately, with task-type and domain breakdowns where sample sizes permit. Document infrastructure exclusions and reruns, including their counts and reasons. State the limits of small samples.

## Human review

Hide setting labels and randomize output order. Calibrate reviewers on development examples before final scoring. Use two independent reviewers where feasible.

Preserve original scores, disagreements, adjudicated scores, and reasons. Report double-review coverage and the agreement statistic used. Model-based scoring may assist review but does not replace legal annotation verification.

## Failure analysis

Classify failures by fact extraction, clarification, retrieval, version applicability, citation, calculation, tool execution, workflow control, or drafting. For each case, record task/run identifiers, observable evidence, impact, and a proposed improvement.

Separate observed failures from unverified root-cause hypotheses. Include both successful recovery and unresolved failure when comparing settings.
