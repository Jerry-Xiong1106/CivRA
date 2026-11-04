# Evaluation interface requirements

Status: Draft v0.1 — proposed by Member 5; not yet agreed with interface owners.

## 1. Scope and ownership

This checklist specifies what the evaluation runner and three baselines need from other modules. Names and fields below are proposed contracts, not existing implementations. Owners may offer equivalent interfaces; Member 5 will map agreed differences in `evaluation/adapters/`.

Prefer callable Python interfaces for offline experiments. Signatures below are asynchronous Python-style pseudocode, not a dependency or framework requirement. A JSON HTTP interface is acceptable when a module runs as a separate service. An implementation needs one agreed transport, not both. Streaming and frontend integration are not prerequisites for the first evaluation run.

Keep initial contracts in this document. Move accepted shared definitions into `backend/common/` only after owner review. Member 5 owns evaluation task/result schemas; domain types remain with their respective owners.

| Owner | Requested interfaces | First integration milestone |
| --- | --- | --- |
| Member 2 | `search_statutes`, `search_cases`, `get_source`, `get_knowledge_manifest` | A query returns locatable, versioned results or an explicit empty result |
| Member 3 | `parse_documents`, `check_evidence`, `calculate_costs`, `compare_routes`, `draft_document`, `verify_output` | One selected pilot task runs through its required business tools |
| Member 4 | `generate`, Agent run lifecycle, `export_trace`, `get_runtime_manifest` | Direct LLM runs; Agent can pause for clarification and continue |
| Member 1 | Case/file storage and output retrieval, if required by backend execution | One isolated evaluation case and its files can be created and read |
| Member 5 | Task loader, disclosure controller, baselines, adapters, scoring and experiment records | The same task can run through each available setting |

Member 1 is an optional integration dependency when local adapters can supply equivalent storage. Members 2–4 remain responsible for their module implementations; evaluation does not duplicate them.

## 2. Naming and wire conventions

| Item | Proposed convention |
| --- | --- |
| Functions and JSON fields | `snake_case` |
| Type names | `PascalCase` |
| Identifiers | Opaque strings, unique in their declared scope |
| References | `case_id`, `file_id`, `source_id`, `run_id`, `step_id`, `request_id` |
| Contract version | `schema_version: "0.1"` on top-level requests and results |
| Date | `YYYY-MM-DD`; unknown dates are `null` |
| Timestamp | ISO 8601 UTC, such as `2026-11-02T12:00:00Z` |
| Duration | Non-negative integer milliseconds, fields ending in `_ms` |
| Money | Decimal string with an explicit currency; never a binary floating-point amount |
| Unknown measurement | `null`, not zero or an empty string |
| Collections | Empty array when known to be empty |
| Enumerations | Lowercase strings; document allowed values |

Unless marked optional, listed fields are required. Nullable fields must still be present when the value is unknown. Owner-specific domain objects must have a versioned field definition and an example before integration; a type name alone is not a contract.

### Invocation context

Each model/tool invocation accepts an `InvocationContext` containing:

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | `string` | Contract version |
| `request_id` | `string` | Unique identifier for this invocation attempt |
| `case_id` | `string \| null` | Isolated evaluation case; `null` only before case creation |
| `experiment_run_id` | `string` | Member 5's record for one setting/task/repetition |
| `deadline_at` | `string \| null` | Absolute execution deadline |

Agent execution also has its own `run_id`. Preserve its association with `experiment_run_id`. A resumed Agent keeps its `run_id`; an independent repetition receives a new case and experiment record. Correlation identifiers are not authorization credentials.

### Invocation result

Ordinary model/tool/storage calls return this envelope. `data` is the interface-specific payload; `error` is `null` on success. An empty search is a successful call with no matches. Agent lifecycle state is carried inside `data`, separate from invocation success.

```json
{
  "schema_version": "0.1",
  "request_id": "request-001",
  "status": "ok",
  "data": {},
  "error": null,
  "meta": {
    "duration_ms": 85,
    "implementation_version": "owner-supplied-version"
  }
}
```

`status` is `ok` or `error`. For errors, `data` is `null` and `error` contains `code: string`, `message: string`, `retryable: boolean`, and `details: object | null`. Proposed codes include `invalid_request`, `not_found`, `version_conflict`, `timeout`, `rate_limited`, `provider_error`, `tool_error`, and `cancelled`.

Owners document internal retries and expose attempt counts. Member 5 must not unknowingly add a second retry loop. Logs must not contain credentials or hidden evaluation annotations.

## 3. Member 2: retrieval and sources

### Interfaces

| Signature | Request fields beyond context | Result payload |
| --- | --- | --- |
| `search_statutes(request: StatuteSearchRequest) -> SearchResult` | `query: string`, `filters: SearchFilters`, `top_k: integer`, `knowledge_version: string` | `matches: SearchMatch[]`, `empty_reason: string \| null`, `knowledge_version: string` |
| `search_cases(request: CaseSearchRequest) -> SearchResult` | Same common search fields; case-specific filters documented by owner | Same search result format |
| `get_source(request: SourceRequest) -> SourceRecord` | `source_id: string`, `source_version: string`, `locator: SourceLocator \| null` | Original text or permitted excerpt, resolved location, and `SourceRef` |
| `get_knowledge_manifest() -> KnowledgeManifest` | None | Snapshot identity, build/index versions, coverage, and exclusion-manifest reference |

`SearchFilters` supports nullable `domain`, `jurisdiction`, `incident_date`, and `as_of_date`; case searches may also use a decision-date range. Document each filter's meaning. Reject unsupported filters rather than silently ignoring them. Queries may be Chinese; field names remain English.

`SearchMatch` contains `text: string`, `source_ref: SourceRef`, `rank: integer`, and `score: number | null`. Document score meaning; scores are not assumed comparable across search methods.

### Source reference

Member 2 owns `SourceRef`. Proposed fields:

- `source_id`, `source_type`, `title`, `source_version`: strings identifying the source and version.
- `url: string | null`: original source URL, if available.
- `locator: SourceLocator`: applicable `article`, `page`, `paragraph`, or `chunk_id` fields; at least one populated.
- `effective_from`, `effective_to`, `decision_date`: nullable dates, populated where applicable.
- `retrieved_at`: timestamp.

`get_source` must resolve the same version returned by retrieval, not silently substitute the newest text. If access is unavailable, return an explicit error or documented access limitation.

### Needed for evaluation

- One query/result fixture and one no-match fixture.
- A reproducible knowledge snapshot or manifest with corpus/index versions.
- A way to verify that final-test source judgments and related answer-revealing cases are excluded. Member 5 supplies the controlled case-family exclusion list; record its version or digest without exposing gold answers to retrieval.
- Relevant-authority and retrieval-relevance annotations for pilot review.

## 4. Member 3: case and business tools

All requests include context and the relevant `case_version: integer` when they depend on case state. All results identify the version used. Member 3 owns the domain schemas; this table lists required information, not complete business algorithms.

| Interface | Required input | Required output information |
| --- | --- | --- |
| `parse_documents` | `file_ids: string[]` | Extracted text/fields, file/page/span references, extraction or OCR warnings |
| `check_evidence` | `case_snapshot: CaseSnapshot` | Missing facts, conflicts, duplicates, material references, and suggested questions |
| `calculate_costs` | Versioned calculation type, `inputs`, `rule_refs: SourceRef[]` | Decimal amounts and currency, formula, inputs, assumptions, rule references, unresolved inputs |
| `compare_routes` | Snapshot, available authorities, calculation results, user goals | Route options, prerequisites, supported costs/steps, uncertainties, and supporting references |
| `draft_document` | Confirmed facts, route, `template_id`, `template_version`, source references | Text or `artifact_id`, missing fields, fact/evidence links, and template/version used |
| `verify_output` | Candidate output, snapshot, cited sources | Findings by severity, affected output locations, supporting references, and suggested corrections |

`CaseSnapshot` needs at least `case_id`, `case_version`, `facts`, `evidence`, `claims`, and nullable `selected_route`. Facts distinguish claims from confirmed information. Evidence records contain material identifiers and source locations. Exact field definitions require Member 3's approval.

Use proposed route identifiers `negotiation`, `mediation`, and `litigation`. Unknown values stay explicit. Tool findings are data; they do not themselves pause execution or select the user's route. Member 4 controls Agent decisions; Member 5 controls the fixed baseline sequence.

If `update_case` is exposed, it must accept `expected_case_version`, return the new version, and report stale-write conflicts. Member 3 defines the state change; Member 1 may persist it. Do not implement independent competing case stores in evaluation.

### Needed for evaluation

- Prioritize tools required by the first pilot task; not every tool is an initial blocker.
- Provide one valid request/result fixture per integrated tool and an invalid-input fixture.
- Document calculations and known limitations so expected results can be checked.
- Review pilot facts, evidence issues, document requirements, and acceptable outputs. A tool's own verification result is not an independent gold label.

## 5. Member 4: model access and Agent execution

### Shared model interface

`generate(request: ModelRequest) -> ModelResponse`

| Request field beyond context | Type / requirement |
| --- | --- |
| `model` | Model identifier |
| `messages` | Ordered message objects |
| `generation` | Explicit parameters, including output-token limit; nullable seed where supported |
| `tools` | Tool specifications; empty for Direct LLM |
| `tool_choice` | Agreed provider-neutral selection value or explicit unsupported response |

Messages need `role` (`system`, `user`, `assistant`, `tool`), nullable `content`, assistant `tool_calls`, and tool-message `tool_call_id`. Each tool call contains `call_id`, `tool_name`, and parsed JSON-object `arguments`. Each tool specification contains `name`, `description`, and an `input_schema` JSON Schema. Validate arguments before execution. Document provider-specific constraints in the adapter.

The response needs `content: string | null`, `tool_calls: ToolCall[]`, `finish_reason: string`, resolved model identity, provider request identifier if available, and usage/cost metadata. A tool-call response may have no text.

Usage fields are nullable `input_tokens`, `output_tokens`, `total_tokens`, plus `attempt_count` and any separate cache/reasoning counts supported by the provider. Document overlapping counts to avoid double-counting. Cost metadata identifies amount, currency, billed/estimated basis, and price version, or is `null`.

Unsupported generation options must be declared; do not silently change parameters between settings. The same interface serves the baselines and Agent, including repeated model calls in Fixed Workflow.

### Agent lifecycle

| Interface | Required request | Result |
| --- | --- | --- |
| `start_run` | Context, public case snapshot/material references, user message, runtime config, budgets | `RunSnapshot` |
| `get_run` | `run_id` | Current `RunSnapshot` |
| `continue_run` | `run_id`, `request_id`, `expected_case_version`, `wait_id`, user reply or selected route | Updated `RunSnapshot` |
| `cancel_run` | `run_id`, `request_id` | Acknowledgment and current state; poll until terminal |
| `export_trace` | `run_id`, optional cursor | Ordered events, nullable next cursor, and completeness indicator |
| `get_runtime_manifest` | No request parameters | Runtime, prompt, tool-schema, and model configuration versions |

Proposed lifecycle states: `queued`, `running`, `waiting_user`, `completed`, `failed`, `cancelled`, `budget_exhausted`. The last four are terminal. `RunSnapshot` contains `run_id`, `case_id`, `case_version`, `status`, nullable `wait`, nullable `output`, nullable `error`, and usage summary. Define whether lifecycle methods return immediately or wait; both must support an execution deadline.

When waiting, expose a structured request:

```json
{
  "run_id": "agent-run-001",
  "case_id": "eval-case-001",
  "case_version": 1,
  "status": "waiting_user",
  "wait": {
    "wait_id": "wait-001",
    "kind": "clarification",
    "questions": [
      {"question_id": "question-001", "text": "Has any amount been repaid?"}
    ],
    "options": []
  },
  "output": null,
  "error": null
}
```

Other proposed wait kinds are `route_selection` and `material_request`. The disclosure controller in Member 5's runner reads hidden facts and returns only permitted replies. Agent requests never include gold annotations or the hidden-fact pool. For route selection, follow the task's predefined user preference; do not let the evaluator improvise it.

`continue_run` must reject stale wait identifiers or versions without duplicate execution. Document how request identifiers prevent duplicate starts/resumes and how to recover after a client timeout. Each repetition starts with isolated state; resumes retain the same Agent run.

### Outputs and traces

Final or partial outputs need answer text, cited `SourceRef` objects linked to output locations, artifact references, unresolved items, case/output versions, and a completion reason. Retain partial output on failure or budget exhaustion when available. Outputs must be readable without the frontend.

Trace events need `event_id`, monotonic per-run `sequence`, `run_id`, `step_id`, optional parent step, event type, model/tool name as applicable, status, timestamps, duration, input/output references, source references, versions, error, and usage where applicable. Export referenced inputs/results through the same controlled access path so Member 5 can inspect tool arguments and feedback.

Record actual calls, retries, waits, and errors. Export observable actions and decision summaries, not private model reasoning. Identify authoritative usage totals and whether summaries include retries; evaluation must not sum the same calls from both events and summaries.

Runtime enforces internal call/token/time budgets; Member 5 enforces experiment-level limits. Agree on accounting boundaries so limits and costs are not counted twice.

## 6. Member 1: optional storage/service integration

Use these only if Agent/tools require application-managed cases or files. Equivalent local storage is acceptable if it preserves the same identifiers and isolation.

| Callable name | Request | Result |
| --- | --- | --- |
| `create_case` | Context, public initial case data | `case_id`, `case_version` |
| `upload_file` | `case_id`, filename, media type, bytes/stream | `file_id`, checksum, size, case association |
| `get_case` | `case_id`, optional version | Versioned public snapshot |
| `get_file` | `case_id`, `file_id` | File content/stream and media type |
| `get_artifact` | `case_id`, `artifact_id` | Output content/stream, media type, and version |

For HTTP transport, proposed routes are `POST /api/v1/cases`, `POST /api/v1/cases/{case_id}/files`, `GET /api/v1/cases/{case_id}`, `GET /api/v1/cases/{case_id}/files/{file_id}`, and `GET /api/v1/cases/{case_id}/artifacts/{artifact_id}`. Use multipart uploads and binary downloads where appropriate; ordinary JSON responses use the agreed envelope. Document authentication, request limits, and status-code mappings. These route names require Member 1's confirmation.

Keep evaluation data isolated from other cases. Never upload gold annotations with public materials. Store credentials in local environment configuration, not task JSON or traces. Agent lifecycle and trace ownership remain with Member 4 even if Member 1 exposes HTTP routes.

## 7. Fixtures and acceptance checks

Owners should deliver a callable entry point or service address, setup/configuration instructions, contract version, limitations, and synthetic request/result fixtures. Fixtures must contain no personal data or secrets.

| Check | Expected result | Owner / reviewer |
| --- | --- | --- |
| Citation resolution | A retrieved reference resolves to the recorded text/version | Member 2 / Member 5 |
| No retrieval matches | Successful empty result, not a tool failure | Member 2 / Member 5 |
| Missing/conflicting evidence | Findings identify affected facts and material locations | Member 3 / Member 5 |
| Model tool-call exchange | Tool-call identifiers and JSON arguments survive a request/response cycle | Member 4 / Member 5 |
| Clarification and resume | Runner discloses only permitted facts; Agent continues the same run | Members 4 and 5 |
| Timeout/retry | Failure and all attempts are visible without duplicate work | Interface owner / Member 5 |
| Trace accounting | Usage, retries, and referenced results can be inspected without double-counting | Member 4 / Member 5 |
| Case isolation | Independent repetitions cannot read each other's materials/state | Members 1, 4 and 5 |
| Version conflict | Stale updates/resumes are rejected explicitly | Members 1, 3, 4 as applicable / Member 5 |

## 8. Delivery order and decisions

1. Member 5 drafts task/result schemas and mocks against these proposed contracts.
2. Members 2–4 confirm names, transports, minimum fields, and one fixture each. Member 1 confirms storage only if required.
3. Integrate shared model access for Direct LLM, then retrieval for RAG.
4. Integrate pilot-required business tools for Fixed Workflow.
5. Integrate Agent start/wait/resume/output/trace for the four-setting pilot.
6. Resolve schema gaps, leakage checks, retry/budget accounting, and versioning before freezing formal experiments.

Open decisions: actual module import paths or service URLs; synchronous versus asynchronous implementation; field/schema ownership details; supported filters/model options; budget values; artifact retention; trace access; and contract-change policy. Record owner responses here before treating any proposed interface as agreed.

Related documents: [protocol](protocol.md), [annotation guide](annotation_guide.md), [rubric](rubric.md), and [module README](../../evaluation/README.md).
