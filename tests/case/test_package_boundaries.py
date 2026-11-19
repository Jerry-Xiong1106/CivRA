"""Regression checks for the case package's dependency and storage boundaries."""
import subprocess
import sys
from pathlib import Path

import pytest

from backend.case.contracts import CaseRequest, InvocationContext


def test_importing_models_keeps_services_and_integrations_unloaded():
    code = """
import sys
from backend.case import CaseSnapshot, Money
for prefix in ('backend.case.service', 'backend.case.drafting', 'backend.case.materials',
               'backend.agent', 'backend.tools', 'langchain', 'paddleocr'):
    assert not any(n == prefix or n.startswith(prefix + '.') for n in sys.modules), prefix
"""
    completed = subprocess.run([sys.executable, "-B", "-c", code],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr


def test_business_service_import_does_not_load_agent_or_tool_adapters():
    code = """
import sys
from backend.case import CaseService
assert CaseService.__name__ == 'CaseService'
for prefix in ('backend.agent', 'backend.tools', 'evaluation', 'langchain', 'paddleocr'):
    assert not any(n == prefix or n.startswith(prefix + '.') for n in sys.modules), prefix
"""
    completed = subprocess.run([sys.executable, "-B", "-c", code],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr


def test_custom_repository_uses_injected_storage_and_intake_facade(tmp_path):
    from backend.case import CaseService, SQLiteCaseRepository
    from scripts.case.fixtures import load_development_case
    from backend.case.storage.assets import SQLiteAssetStore

    repository = SQLiteCaseRepository(tmp_path / "case.sqlite3")
    case = repository.create_case(load_development_case())
    assets = SQLiteAssetStore(repository)

    class RepositoryPort:
        # No SQLite connection API is exposed.
        get_case = repository.get_case
        find_update = repository.find_update
        commit_update = repository.commit_update

    custom = RepositoryPort()
    with pytest.raises(ValueError, match="asset store"):
        CaseService(custom)
    service = CaseService(custom, assets=assets)
    request = CaseRequest(context=InvocationContext(request_id="summary", case_id=case.case_id,
        experiment_run_id=None, deadline_at=None), case_version=case.case_version)
    result = service.summarize_case(request)
    assert result.status != "error"
    assert result.data.facts
    assert service.activity is None
    assert repository.get_case(case.case_id) == case


def test_sqlite_connection_rolls_back_failed_write(tmp_path):
    from backend.case import SQLiteCaseRepository
    from scripts.case.fixtures import load_development_case

    repository = SQLiteCaseRepository(tmp_path / "case.sqlite3")
    case = repository.create_case(load_development_case())
    with pytest.raises(RuntimeError, match="abort"):
        with repository.connection(write=True) as connection:
            connection.execute("UPDATE case_heads SET version=99 WHERE case_id=?", (case.case_id,))
            raise RuntimeError("abort")
    assert repository.get_case(case.case_id) == case
