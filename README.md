# CivRA

An agentic AI system for evidence-grounded civil law assistance.

## Member 3 case tools

The case business layer includes versioned facts/evidence, local storage, document parsing,
calculations, drafting and verification. See the [integration handoff](docs/case/submission-handoff.md)
for installation, the ten Python tools, source resolution, principal-balance calculations,
answer citation checks, and the remaining model/UI integration work.

## Project structure

- `frontend/`: frontend pages and user interaction
- `backend/api/`: backend APIs and streaming responses
- `backend/agent/`: agent runtime, planning, and workflow orchestration
- `backend/knowledge/`: legal knowledge base, case database, and RAG
- `backend/case/`: case facts, evidence, and legal business logic
- `backend/tools/`: registration and adaptation of tools callable by the agent
- `backend/common/`: shared schemas, protocols, and configuration
- `evaluation/`: datasets, experiments, metrics, and reports
- `data/`: legal materials, case materials, templates, and sample data
- `tests/`: unit and integration tests
- `docs/`: architecture and interface documentation
- `scripts/`: data processing and development scripts

Each module may organize its internal files according to its owner's implementation needs. Cross-module data structures and interfaces should be documented in `backend/common/` and `docs/`.

## Scripts and tests

| Module | Scripts | Tests |
| --- | --- | --- |
| Agent | `scripts/agent/run_agent.py` | `tests/agent/` |
| Retrieval / RAG | `scripts/rag/` | `tests/rag/` |
| Evaluation | `scripts/evaluation/` (run, validate, review) | `tests/evaluation/` |

Run checks from the repository root:

```powershell
.\.venv-rag\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
.\.venv-rag\Scripts\python.exe scripts/agent/run_agent.py --dry-run --mock-statutes
.\.venv-rag\Scripts\python.exe scripts/evaluation/validate_evaluation_tasks.py
```

The main Agent CLI replaces the separate mock demo script. Run, validate and review remain separate evaluation commands.
