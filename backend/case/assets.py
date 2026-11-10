"""Versioned input files, extracted text and output artifacts in the shared SQLite store."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from pydantic import Field

from .models import BusinessOutput, ContractModel, Identifier, SourcePointer
from .repository import CaseNotFound, SQLiteCaseRepository, VersionConflict


class FileRecord(ContractModel):
    case_id: Identifier
    file_id: Identifier
    file_version: Identifier
    file_name: Identifier
    media_type: Identifier
    document_type: Identifier
    content_sha256: str
    size_bytes: int = Field(ge=0)


class CaseAssetStore:
    """Uses the same DB as the case repository; caller owns authorization to case_id."""

    def __init__(self, repository: SQLiteCaseRepository):
        self.repository = repository
        with repository._connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS case_files (
                    case_id TEXT NOT NULL, file_id TEXT NOT NULL, file_version TEXT NOT NULL,
                    metadata_json TEXT NOT NULL, content BLOB NOT NULL,
                    PRIMARY KEY(case_id,file_id,file_version),
                    FOREIGN KEY(case_id) REFERENCES case_heads(case_id)
                );
                CREATE TABLE IF NOT EXISTS case_extractions (
                    case_id TEXT NOT NULL,file_id TEXT NOT NULL,file_version TEXT NOT NULL,
                    extraction_version TEXT NOT NULL,page_key TEXT NOT NULL,text TEXT NOT NULL,
                    PRIMARY KEY(case_id,file_id,file_version,extraction_version,page_key),
                    FOREIGN KEY(case_id,file_id,file_version) REFERENCES case_files(case_id,file_id,file_version)
                );
                CREATE TABLE IF NOT EXISTS case_artifacts (
                    case_id TEXT NOT NULL,artifact_id TEXT NOT NULL,filename TEXT NOT NULL,
                    media_type TEXT NOT NULL,content BLOB NOT NULL,
                    PRIMARY KEY(case_id,artifact_id),FOREIGN KEY(case_id) REFERENCES case_heads(case_id)
                );
                CREATE TABLE IF NOT EXISTS case_outputs (
                    case_id TEXT NOT NULL,output_id TEXT NOT NULL,output_version INTEGER NOT NULL,
                    case_version INTEGER NOT NULL,output_json TEXT NOT NULL,
                    PRIMARY KEY(case_id,output_id,output_version),
                    FOREIGN KEY(case_id,case_version) REFERENCES case_versions(case_id,version)
                );
            """)

    def put_file(self, *, case_id, file_id, file_name, media_type, document_type, content: bytes, file_version="1") -> FileRecord:
        self.repository.get_case(case_id)
        if len(content) > 20 * 1024 * 1024:
            raise ValueError("first-version file limit is 20 MiB")
        record = FileRecord(case_id=case_id,file_id=file_id,file_version=file_version,file_name=file_name,
            media_type=media_type,document_type=document_type,content_sha256=hashlib.sha256(content).hexdigest(),size_bytes=len(content))
        with self.repository._connection(write=True) as connection:
            old=connection.execute("SELECT metadata_json,content FROM case_files WHERE case_id=? AND file_id=? AND file_version=?",
                (case_id,file_id,file_version)).fetchone()
            if old:
                if FileRecord.model_validate_json(old[0]) != record or old[1] != content:
                    raise ValueError("file version is immutable; use a new file_version")
            else:
                connection.execute("INSERT INTO case_files VALUES (?,?,?,?,?)",(case_id,file_id,file_version,record.model_dump_json(),content))
        return record

    def get_file(self, case_id, file_id, file_version=None) -> tuple[FileRecord, bytes]:
        with self.repository._connection() as connection:
            rows=connection.execute("SELECT metadata_json,content FROM case_files WHERE case_id=? AND file_id=?" +
                (" AND file_version=?" if file_version is not None else ""),
                (case_id,file_id,file_version) if file_version is not None else (case_id,file_id)).fetchall()
        if not rows:
            raise CaseNotFound("file does not exist in this case")
        if len(rows) != 1:
            raise ValueError("multiple file versions exist; specify file_version explicitly")
        return FileRecord.model_validate_json(rows[0][0]), rows[0][1]

    def save_extraction(self, record: FileRecord, extraction_version: str, page_key: str, text: str):
        with self.repository._connection(write=True) as connection:
            old=connection.execute("SELECT text FROM case_extractions WHERE case_id=? AND file_id=? AND file_version=? AND extraction_version=? AND page_key=?",
                (record.case_id,record.file_id,record.file_version,extraction_version,page_key)).fetchone()
            if old and old[0] != text:
                raise ValueError("an extraction version cannot identify two different texts")
            connection.execute("INSERT OR IGNORE INTO case_extractions VALUES (?,?,?,?,?,?)",
                (record.case_id,record.file_id,record.file_version,extraction_version,page_key,text))

    def read_extraction(self, case_id, material_ref) -> str:
        if material_ref.extraction_version is None:
            raise ValueError("an extraction_version is required to resolve extracted text")
        page_key=str(material_ref.page) if material_ref.page is not None else material_ref.paragraph or "text"
        with self.repository._connection() as connection:
            row=connection.execute("SELECT text FROM case_extractions WHERE case_id=? AND file_id=? AND file_version=? AND extraction_version=? AND page_key=?",
                (case_id,material_ref.file_id,material_ref.file_version,material_ref.extraction_version,page_key)).fetchone()
        if row is None:
            raise CaseNotFound("extracted text or version is unavailable")
        text=row[0]
        if material_ref.span_start is not None:
            if material_ref.span_end > len(text):
                raise ValueError("material span exceeds extracted text")
            text=text[material_ref.span_start:material_ref.span_end]
        if material_ref.quote is not None and material_ref.quote != text:
            raise ValueError("stored text does not match quoted material")
        return text

    def put_artifact(self, case_id: str, filename: str, media_type: str, content: bytes) -> str:
        artifact_id="artifact_"+hashlib.sha256(case_id.encode()+b"\0"+filename.encode()+b"\0"+content).hexdigest()
        with self.repository._connection(write=True) as connection:
            connection.execute("INSERT OR IGNORE INTO case_artifacts VALUES (?,?,?,?,?)",(case_id,artifact_id,filename,media_type,content))
        return artifact_id

    def get_artifact(self, case_id, artifact_id) -> dict:
        with self.repository._connection() as connection:
            row=connection.execute("SELECT filename,media_type,content FROM case_artifacts WHERE case_id=? AND artifact_id=?",(case_id,artifact_id)).fetchone()
        if row is None:
            raise CaseNotFound("artifact does not exist in this case")
        return {"filename":row[0],"media_type":row[1],"content":row[2]}

    def save_output(self, output: BusinessOutput, *, deadline_at=None) -> BusinessOutput:
        output=BusinessOutput.model_validate(output.model_dump(mode="json"))
        with self.repository._connection(write=True) as connection:
            return self._save_output(connection, output, deadline_at=deadline_at)

    @staticmethod
    def artifact_identity(case_id, filename, content):
        return "artifact_"+hashlib.sha256(case_id.encode()+b"\0"+filename.encode()+b"\0"+content).hexdigest()

    def save_output_bundle(self, output: BusinessOutput, artifacts: list[dict], *, deadline_at=None) -> BusinessOutput:
        """Commit all export bytes and their output together, or roll back all of them."""
        output=BusinessOutput.model_validate(output.model_dump(mode="json"))
        if not artifacts or len(artifacts)>3:
            raise ValueError("a document bundle needs one to three artifacts")
        ids=set()
        for item in artifacts:
            if not isinstance(item["content"],bytes) or not 0<len(item["content"])<=20*1024*1024:
                raise ValueError("invalid or oversized export content")
            expected=self.artifact_identity(output.case_id,item["filename"],item["content"])
            if item["artifact_id"]!=expected or expected in ids:
                raise ValueError("bundle artifact identity mismatch or duplicate")
            ids.add(expected)
        declared=output.structured_data.get("artifacts",{}) if output.structured_data else {}
        if set(declared.values())!=ids or output.artifact_id not in ids:
            raise ValueError("output artifact manifest does not match the export bundle")
        with self.repository._connection(write=True) as connection:
            for item in artifacts:
                connection.execute("INSERT OR IGNORE INTO case_artifacts VALUES (?,?,?,?,?)",
                    (output.case_id,item["artifact_id"],item["filename"],item["media_type"],item["content"]))
            return self._save_output(connection,output,deadline_at=deadline_at)

    @staticmethod
    def _save_output(connection, output, *, deadline_at=None):
        if deadline_at is not None and datetime.now(timezone.utc)>=deadline_at:
            raise TimeoutError("output deadline expired while awaiting storage")
        row=connection.execute("SELECT version FROM case_heads WHERE case_id=?",(output.case_id,)).fetchone()
        if row is None:
            raise CaseNotFound(output.case_id)
        if row[0] != output.case_version:
            raise VersionConflict(output.case_version,row[0])
        old=connection.execute("SELECT output_json FROM case_outputs WHERE case_id=? AND output_id=? AND output_version=?",
            (output.case_id,output.output_id,output.output_version)).fetchone()
        if old:
            if BusinessOutput.model_validate_json(old[0]) != output:
                raise ValueError("output version is immutable")
            return output
        latest=connection.execute("SELECT MAX(output_version) FROM case_outputs WHERE case_id=? AND output_id=?",(output.case_id,output.output_id)).fetchone()[0]
        if output.output_version != (latest or 0)+1:
            raise ValueError("output_version must increment by exactly one")
        if output.artifact_id:
            if not connection.execute("SELECT 1 FROM case_artifacts WHERE case_id=? AND artifact_id=?",(output.case_id,output.artifact_id)).fetchone():
                raise ValueError("output references an unknown artifact")
        connection.execute("INSERT INTO case_outputs VALUES (?,?,?,?,?)",(output.case_id,output.output_id,output.output_version,output.case_version,output.model_dump_json()))
        return output

    def get_output(self, case_id, output_id, output_version=None) -> dict:
        with self.repository._connection() as connection:
            row=connection.execute("SELECT output_json FROM case_outputs WHERE case_id=? AND output_id=?"+
                (" AND output_version=?" if output_version is not None else " ORDER BY output_version DESC LIMIT 1"),
                (case_id,output_id,output_version) if output_version is not None else (case_id,output_id)).fetchone()
        if row is None:
            raise CaseNotFound("output does not exist in this case")
        output=BusinessOutput.model_validate_json(row[0])
        return {"output":output,"stale":output.is_stale_for(self.repository.get_case(case_id))}

    def next_output_version(self, case_id, output_id) -> int:
        with self.repository._connection() as connection:
            latest=connection.execute("SELECT MAX(output_version) FROM case_outputs WHERE case_id=? AND output_id=?",(case_id,output_id)).fetchone()[0]
        return (latest or 0)+1

    def list_case_inventory(self,case_id,*,limit=500):
        """One bounded read of input versions and latest version of each output."""
        if type(limit) is not int or not 1<=limit<=500:
            raise ValueError("invalid inventory limit")
        with self.repository._connection() as connection:
            connection.execute("BEGIN")
            head=connection.execute("SELECT version FROM case_heads WHERE case_id=?",(case_id,)).fetchone()
            if head is None: raise CaseNotFound(case_id)
            files=connection.execute("SELECT metadata_json FROM case_files WHERE case_id=? ORDER BY file_id,file_version LIMIT ?",(case_id,limit+1)).fetchall()
            outputs=connection.execute("SELECT o.output_json FROM case_outputs o WHERE o.case_id=? AND o.output_version=(SELECT MAX(v.output_version) FROM case_outputs v WHERE v.case_id=o.case_id AND v.output_id=o.output_id) ORDER BY o.output_id LIMIT ?",(case_id,limit+1)).fetchall()
        return dict(case_version=head[0],files=[FileRecord.model_validate_json(r[0]) for r in files[:limit]],
            outputs=[BusinessOutput.model_validate_json(r[0]) for r in outputs[:limit]],truncated=len(files)>limit or len(outputs)>limit)
