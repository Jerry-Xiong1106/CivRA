# Annotation guide

Status: Draft v0.1. Owner: Member 5.

## Task coverage

Prepare tasks within the agreed civil-dispute scope: contracts, lending, leasing, and tort compensation. For the pilot, aim for two tasks in each category: complete information, missing information, conflicting evidence, ambiguous requests, and multiple issues. Multiple tags are allowed; identify a primary objective.

Exclude complex disability assessments, professional medical judgments, and complex insurance-contract disputes from core tasks.

Use de-identified public cases or explicitly labeled synthetic scenarios. Synthetic tasks still require verified legal annotations before formal testing. Model-generated answers are drafts requiring review, not legal ground truth.

## Information layers

| Layer | Contents | Access |
| --- | --- | --- |
| Public input | Initial description and permitted materials | Available to the evaluated setting |
| Hidden facts | Additional information and disclosure rules | Available only through the interaction controller |
| Gold annotations | Key facts, authorities, acceptable conclusions, and scoring items | Available to reviewers and scoring code only |

Store and load these layers separately. Gold annotations must not enter model prompts, tool responses, or retrieval indexes. Directory names alone do not establish isolation; inspect the actual loading paths and recorded inputs.

## Proposed task fields

Concrete schemas will be added under `evaluation/schemas/` after the pilot format is reviewed.

| Field | Purpose |
| --- | --- |
| `task_id` | Unique task identifier |
| `source_case_id` | Original case or case-family identifier for split isolation |
| `split` | Pilot/development/final-test assignment; pilot is development data |
| `task_type`, `domain`, `tags` | Interaction type, legal domain, and coverage categories |
| `initial_input` | User description supplied at the start |
| `available_materials` | Material identifiers, locators, and access scope |
| `timeline` | Relevant dates, with unknowns explicitly marked |
| `user_goal`, `selected_route` | Requested outcome and any confirmed route choice |
| `hidden_facts` | Facts, disclosure conditions, and standard replies; stored separately |
| `gold_annotations` | Review-only facts, issues, authorities, acceptable outputs, and scoring items |
| `provenance` | Original sources, synthetic status, and usage conditions |
| `review_status`, `version` | Review decisions and revision history |

## Facts and evidence

Distinguish user claims, statements in materials, confirmed facts, and inferences. Link important facts to material identifiers and source locations. Preserve conflicting accounts rather than selecting one without support.

Annotate missing information and explain which conclusion or action it affects. Never fill a gap with an invented fact. Record calculations with inputs, units, assumptions, and expected results.

## Legal authorities and acceptable outputs

Record each authority's source, provision, version, effective information, and retrieval date. Check relevance to the incident timeline and issue. For reference cases, record similarities, differences, and limits on comparison.

List required issues, factual premises for acceptable conclusions, necessary citations, and unresolved questions. Accept multiple legally supported answers; do not require exact wording.

Respect the user's route choice. Voluntary negotiation or mediation is not a universal prerequisite. Require a complaint only when the task requests litigation drafting. Do not annotate uncalibrated winning probabilities as gold answers.

For each scoring item, specify applicability, supporting material, 0–2 criteria, and whether it is a required task-success condition.

## Hidden-fact rules

Each hidden fact needs an identifier, content, disclosure condition, and standard response. Define acceptable semantic requests rather than relying only on exact keywords.

Do not reveal facts that were not requested. Repeated questions return consistent information. Mark unknown or unavailable information explicitly. Log each request, response, matched fact identifier, and ambiguous-request adjudication.

Review disclosure rules with paraphrased questions during the pilot. Freeze the policy before final testing.

## Splits and review

Keep each original case and all derivatives in one split. Tasks inspected during development are not unseen final tests. Check final-test source cases and related judgments against searchable case data and retain the exclusion record.

Member 2 supports authority and retrieval annotations. Member 3 supports facts, evidence, calculations, and business-output annotations. Member 5 maintains the format, split manifest, and review history. All members contribute cross-review.

Tasks with unverified legal annotations remain development-only. Preserve disagreements and their resolution rather than overwriting the original review history.

## Acceptance checklist

- Unique identifiers and complete provenance.
- Clear separation of claims, confirmed facts, and inferences.
- Material locators for important facts and conflicts.
- Explicit missing facts and disclosure conditions.
- Verified authorities and supported alternative conclusions.
- Applicable scoring items and explicit success conditions.
- No identifiable personal information.
- Case-family split isolation and retrieval-leakage checks.
- Recorded review status and version history.
