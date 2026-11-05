# Evaluation protocol

Status: Draft v0.1. Owner: Member 5.

## Comparison settings

| Setting | Execution |
| --- | --- |
| A. Direct LLM | Generate from permitted inputs without retrieval or business-tool calls |
| B. RAG | Follow a predefined retrieval procedure, then generate; no dynamic tool planning |
| C. Fixed Workflow | Call shared capabilities in a predefined sequence |
| D. CivRA Agent | Select tools, clarification, and corrections from case state and feedback |

Member 5 implements A–C. Member 4 supplies D. Specify and freeze the retrieval procedure and fixed workflow before final testing.

## Shared conditions

- Use the same base model, declared generation parameters, initial case materials, and output requirements.
- Retrieval-enabled settings share a knowledge-base snapshot and source rules.
- Fixed Workflow and Agent reuse applicable business tools.
- Version prompts separately for each setting; differences must follow the declared capabilities.
- Declare call, interaction, retry, timeout, and token limits. Record actual consumption as well as limits.

Numerical budgets and repeat counts are selected during the pilot and frozen before final testing. Record provider model identifiers and execution dates when exact model snapshots are unavailable.

## Static and interactive tasks

Static tasks provide identical fixed materials without further disclosures.

Interactive tasks start with identical inputs and use one hidden-fact pool and disclosure policy. Direct LLM remains single-pass; RAG follows its fixed retrieval procedure. Any Fixed Workflow questions follow declared rules; the Agent may ask dynamically within budget. Do not supply unrequested facts automatically.

For each request, record the question, matched fact identifiers, response, and disclosure decision. Define how ambiguous requests and unanswered questions are handled before testing.

Report static and interactive results separately. Interactive comparisons measure information acquisition as well as output quality; settings may obtain different final information. Report which additional facts each setting received rather than attributing all differences to reasoning over identical evidence.

## Task sets and isolation

Start with approximately 10 pilot tasks. The subsequent target is approximately 60 tasks: 20 development and 40 final-test tasks, subject to data quality and review capacity. Pilot tasks count as development data, not an additional unseen test set.

Cover complete information, missing evidence, conflicting evidence, ambiguous requests, and multiple issues. Keep an original case and all derivatives in one split.

Hide final-test gold annotations from all settings. Exclude source judgments, related judgments, and answer-revealing derivatives of final-test cases from searchable case data. Record the exclusion checks and knowledge-base version. Do not tune prompts or workflows on final-test outputs.

## Execution and records

For each run:

1. Load a versioned task and configuration.
2. Validate permitted inputs and select the setting.
3. Execute within the declared limits and apply disclosure rules.
4. Save outputs, trace references, usage, timing, and status.
5. Score against the frozen task checklist and aggregate results.

Run records must identify the task, run, setting, repetition, code revision, dataset version, model and parameters, prompt version, knowledge-base version, and configuration. Preserve outputs, disclosure history, model/tool calls, errors, and retry attempts.

Measure end-to-end latency with declared boundaries and record whether user-wait time is excluded. Distinguish provider-billed cost from estimates; estimates identify currency, usage coverage, and price-table version. Missing usage or cost is unknown, not zero. Mock runs must be labeled and excluded from performance reports.

## Failures and retries

Preserve timeouts, tool failures, budget exhaustion, and empty retrieval results. Empty retrieval is distinct from a failed tool call. Retain all attempts under the frozen retry policy; do not report only the best output.

System failures remain in completion-rate denominators. Score available partial outputs using the rubric. Mark infrastructure incidents separately; exclusions and reruns require recorded reasons and counts. Do not silently drop failed tasks.

## Freeze and reporting

Before final testing, freeze splits, task checklists, prompts, baseline procedures, model parameters, budgets, repetition counts, retry policies, and the knowledge-base snapshot.

Report task quality, task success, execution completion, citation reliability, applicable behavior metrics, resource usage, and robustness. Include sample counts, missing measurements, reviewer coverage, disagreements, and failure cases. Separate static and interactive tasks and show relevant task-type breakdowns.

Post-test changes require a new version and separate results. Optional SFT experiments follow completion of the core comparison.

## Pilot decisions still required

- Model and generation parameters.
- Retrieval parameters and Fixed Workflow steps.
- Budgets, timeouts, retry limits, and repetition count.
- Disclosure matching and ambiguous-request adjudication.
- Timing boundaries, cost accounting, and review assignment.
- Shared interface fields and task-specific success conditions.

These are open decisions, not established team agreements.
