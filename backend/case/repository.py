"""Replaceable SQLite reference repository for local integration and offline evaluation."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .contracts import CaseAuditRecord, UpdateCaseData
from .models import BusinessResult, CaseSnapshot


class CaseNotFound(KeyError):
    pass


class VersionConflict(ValueError):
    def __init__(self, expected: int, actual: int):
        self.expected, self.actual = expected, actual
        super().__init__(f"expected case_version {expected}, current version is {actual}")


class RequestConflict(ValueError):
    pass


class UpdateDeadlineExceeded(TimeoutError):
    pass


class SQLiteCaseRepository:
    """One shared repository for application, tools and evaluation; not an Agent store.

    No open connection is shared between threads. Each write uses BEGIN IMMEDIATE;
    snapshot, audit and retry response are persisted in the same transaction.
    Member 1 may supply an equivalent repository implementing the same protocol.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(path) == ":memory:":
            raise ValueError("use a file-backed database; calls use separate connections")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS case_heads (
                    case_id TEXT PRIMARY KEY, version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS case_versions (
                    case_id TEXT NOT NULL, version INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL, audit_json TEXT NOT NULL,
                    PRIMARY KEY (case_id, version),
                    FOREIGN KEY (case_id) REFERENCES case_heads(case_id)
                );
                CREATE TABLE IF NOT EXISTS case_requests (
                    case_id TEXT NOT NULL, request_id TEXT NOT NULL,
                    request_digest TEXT NOT NULL, response_json TEXT NOT NULL,
                    PRIMARY KEY (case_id, request_id),
                    FOREIGN KEY (case_id) REFERENCES case_heads(case_id)
                );
            """)

    @contextmanager
    def _connection(self, *, write=False):
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.commit()
        except BaseException:
            if write:
                connection.rollback()
            raise
        finally:
            connection.close()

    def create_case(self, snapshot: CaseSnapshot) -> CaseSnapshot:
        snapshot = CaseSnapshot.model_validate(snapshot.model_dump(mode="json"))
        if snapshot.case_version != 1:
            raise ValueError("a new case starts at version 1")
        audit = CaseAuditRecord(case_id=snapshot.case_id, case_version=1, request_id=None,
                                change_reason="create_case", saved_at=datetime.now(timezone.utc))
        with self._connection(write=True) as connection:
            if connection.execute("SELECT 1 FROM case_heads WHERE case_id=?", (snapshot.case_id,)).fetchone():
                raise ValueError("case already exists; it will not be overwritten")
            connection.execute("INSERT INTO case_heads VALUES (?, ?)", (snapshot.case_id, 1))
            connection.execute("INSERT INTO case_versions VALUES (?, ?, ?, ?)",
                               (snapshot.case_id, 1, snapshot.model_dump_json(), audit.model_dump_json()))
        return snapshot.model_copy(deep=True)

    def get_case(self, case_id: str, version: int | None = None) -> CaseSnapshot:
        with self._connection() as connection:
            if version is None:
                row = connection.execute(
                    "SELECT v.snapshot_json FROM case_versions v JOIN case_heads h "
                    "ON v.case_id=h.case_id AND v.version=h.version WHERE h.case_id=?", (case_id,)).fetchone()
            else:
                row = connection.execute("SELECT snapshot_json FROM case_versions WHERE case_id=? AND version=?",
                                         (case_id, version)).fetchone()
        if row is None:
            raise CaseNotFound(f"unknown case or version: {case_id}")
        return CaseSnapshot.model_validate_json(row[0])

    def get_history(self, case_id: str) -> list[CaseAuditRecord]:
        self.get_case(case_id)
        with self._connection() as connection:
            rows = connection.execute("SELECT audit_json FROM case_versions WHERE case_id=? ORDER BY version",
                                      (case_id,)).fetchall()
        return [CaseAuditRecord.model_validate_json(row[0]) for row in rows]

    def list_history(self,case_id,*,after_version=0,through_version=None,limit=50):
        if type(after_version) is not int or after_version<0 or type(limit) is not int or not 1<=limit<=200:
            raise ValueError("invalid history page")
        head=self.get_case(case_id)
        through_version=head.case_version if through_version is None else through_version
        if type(through_version) is not int or not 1<=through_version<=head.case_version:
            raise ValueError("invalid history boundary")
        with self._connection() as connection:
            rows=connection.execute("SELECT audit_json FROM case_versions WHERE case_id=? AND version>? AND version<=? ORDER BY version LIMIT ?",
                (case_id,after_version,through_version,limit+1)).fetchall()
        items=[CaseAuditRecord.model_validate_json(row[0]) for row in rows[:limit]]
        return dict(items=items,through_version=through_version,next_after_version=items[-1].case_version if len(rows)>limit else None)

    @staticmethod
    def _find_update(connection, case_id, request_id, digest):
        row = connection.execute("SELECT request_digest,response_json FROM case_requests WHERE case_id=? AND request_id=?",
                                 (case_id, request_id)).fetchone()
        if row is None:
            return None
        if row[0] != digest:
            raise RequestConflict("request_id was already used for a different update")
        return BusinessResult[UpdateCaseData].model_validate_json(row[1])

    def find_update(self, case_id: str, request_id: str, request_digest: str) -> BusinessResult[UpdateCaseData] | None:
        with self._connection() as connection:
            return self._find_update(connection, case_id, request_id, request_digest)

    @staticmethod
    def _save(connection, snapshot, expected, audit):
        if type(expected) is not int or expected < 1:
            raise ValueError("expected_case_version must be a positive integer")
        row = connection.execute("SELECT version FROM case_heads WHERE case_id=?", (snapshot.case_id,)).fetchone()
        if row is None:
            raise CaseNotFound(snapshot.case_id)
        if row[0] != expected:
            raise VersionConflict(expected, row[0])
        if snapshot.case_version != expected + 1:
            raise ValueError("new case_version must increment by exactly one")
        if (audit.case_id, audit.case_version) != (snapshot.case_id, snapshot.case_version):
            raise ValueError("audit identity/version differs from the snapshot")
        previous = CaseSnapshot.model_validate_json(connection.execute(
            "SELECT snapshot_json FROM case_versions WHERE case_id=? AND version=?",
            (snapshot.case_id, expected)).fetchone()[0])
        if snapshot.created_at != previous.created_at or snapshot.updated_at < previous.updated_at:
            raise ValueError("case creation time is immutable; update time cannot move backwards")
        connection.execute("INSERT INTO case_versions VALUES (?, ?, ?, ?)",
                           (snapshot.case_id, snapshot.case_version, snapshot.model_dump_json(), audit.model_dump_json()))
        connection.execute("UPDATE case_heads SET version=? WHERE case_id=? AND version=?",
                           (snapshot.case_version, snapshot.case_id, expected))

    def replace_case(self, snapshot: CaseSnapshot, *, expected_case_version: int,
                     audit: CaseAuditRecord | None = None) -> CaseSnapshot:
        snapshot = CaseSnapshot.model_validate(snapshot.model_dump(mode="json"))
        audit = audit or CaseAuditRecord(case_id=snapshot.case_id, case_version=snapshot.case_version,
                                        request_id=None, change_reason="replace_case", saved_at=datetime.now(timezone.utc))
        with self._connection(write=True) as connection:
            self._save(connection, snapshot, expected_case_version, audit)
        return snapshot.model_copy(deep=True)

    def commit_update(self, snapshot: CaseSnapshot, *, expected_case_version: int,
                      audit: CaseAuditRecord, request_digest: str,
                      result: BusinessResult[UpdateCaseData],
                      deadline_at: datetime | None = None) -> BusinessResult[UpdateCaseData]:
        snapshot = CaseSnapshot.model_validate(snapshot.model_dump(mode="json"))
        if audit.request_id is None:
            raise ValueError("an idempotent update requires request_id")
        if result.status != "ok" or result.data is None or result.data.case_snapshot != snapshot:
            raise ValueError("persisted update response must contain the committed snapshot")
        with self._connection(write=True) as connection:
            existing = self._find_update(connection, snapshot.case_id, audit.request_id, request_digest)
            if existing is not None:
                return existing
            if deadline_at is not None and datetime.now(timezone.utc) >= deadline_at:
                raise UpdateDeadlineExceeded("update expired while waiting for the database transaction")
            self._save(connection, snapshot, expected_case_version, audit)
            connection.execute("INSERT INTO case_requests VALUES (?, ?, ?, ?)",
                               (snapshot.case_id, audit.request_id, request_digest, result.model_dump_json()))
        return result.model_copy(deep=True)
