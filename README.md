# CivRA

An agentic AI system for evidence-grounded civil law assistance.

## Installation

Use the existing virtual environment or create a Python virtual environment, then install from the repository root:

```powershell
# Backend runtime: Agent, retrieval and case services
.\.venv-rag\Scripts\python.exe -m pip install -r requirements.txt

# Development and tests (includes runtime dependencies)
.\.venv-rag\Scripts\python.exe -m pip install -r requirements-dev.txt

# Optional local OCR; install in the environment running OCR
.\.venv-rag\Scripts\python.exe -m pip install -r requirements-ocr.txt
```

`jsonschema` is a runtime dependency for tool argument validation. `pytest` is development-only.
OCR dependencies remain optional because they are large and require a supported inference environment.

## Frontend HTTP API

See [API setup and frontend contract](backend/api/README.md). The API provides persistent conversations, background Agent execution, resumable questions and route choices, SSE events, material upload and generated document downloads. It is currently a single-process, single-workspace integration; the Vue frontend in this branch currently runs as a local interaction prototype and is not yet connected to this API.

## Vue frontend preview

The guided case workspace is now implemented with Vue 3, Pinia, Vue Router and Vite. Its seven feature tabs, fixed confirmation steps and two reference-case sections preserve the approved HTML design.

From the repository root, run:

```powershell
cd frontend
npm ci
npm run dev
```

Open <http://localhost:8782/>. See [frontend setup and integration boundaries](frontend/README.md) and [migration validation](frontend/验收记录.md). The earlier HTML prototype remains in `frontend/guided-workflow-prototype/` for comparison; the default development entry is now the Vue application in `frontend/`.

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
