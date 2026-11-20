"""Versioned business updates; persistence is injected through a repository protocol."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone

from pydantic import ValidationError

from ..contracts import CaseAuditRecord, IdempotentCaseRepository
from .contracts import UpdateCaseData, UpdateCaseRequest
from ..models import BusinessError, BusinessResult, CaseSnapshot
from ..errors import CaseNotFound, RequestConflict, UpdateDeadlineExceeded, VersionConflict


def update_case(request: UpdateCaseRequest | dict, repository: IdempotentCaseRepository, *, assets=None) -> BusinessResult[UpdateCaseData]:
    """No automatic retry. The repository commits state, audit and retry result together."""
    case_id, version = None, None
    try:
        request = UpdateCaseRequest.model_validate(request.model_dump(mode="json") if isinstance(request, UpdateCaseRequest) else request)
        case_id, version = request.context.case_id, request.expected_case_version
        if assets is not None:
            _validate_material_changes(request, repository, assets)
        digest_payload = request.model_dump(mode="json")
        # An execution deadline may be extended by a client retry; mutation identity stays the same.
        digest_payload["context"].pop("deadline_at", None)
        digest = hashlib.sha256(json.dumps(digest_payload, ensure_ascii=False, sort_keys=True,
                                          separators=(",", ":")).encode("utf-8")).hexdigest()
        existing = repository.find_update(case_id, request.context.request_id, digest)
        if existing is not None:
            return existing
        if request.context.deadline_at is not None and datetime.now(timezone.utc) >= request.context.deadline_at:
            return _error(case_id, version, "timeout", "更新请求已超过截止时间。")
        current = repository.get_case(case_id)
        if current.case_version != version:
            # Another identical attempt may have committed after the first lookup.
            existing = repository.find_update(case_id, request.context.request_id, digest)
            if existing is not None:
                return existing
            raise VersionConflict(version, current.case_version)
        payload = current.model_dump(mode="json")
        changed = {}
        for collection, key in [("statements", "message_id"), ("facts", "fact_id"),
                                ("evidence", "evidence_id"), ("claims", "claim_id")]:
            previous = {item[key]: item for item in payload[collection]}
            changed[collection] = []
            for item in getattr(request.changes, "upsert_" + collection):
                record = item.model_dump(mode="json")
                old = previous.get(record[key])
                if old is not None:
                    if collection == "statements" and old != record:
                        raise ValueError("existing user statements are immutable; use a new message_id")
                    if collection == "facts":
                        immutable = ["field", "value_type", "value", "provenance", "statement_refs",
                                     "evidence_ids", "material_refs", "source_refs", "derivation_ref"]
                        if any(old[name] != record[name] for name in immutable):
                            raise ValueError("fact content/provenance is immutable; add a new fact_id and supersede explicitly")
                    if collection == "evidence":
                        immutable = ["file_id", "file_version", "file_name", "media_type", "document_type",
                                     "content_sha256", "material_refs"]
                        if any(old[name] != record[name] for name in immutable):
                            raise ValueError("evidence material identity is immutable; add a new evidence_id")
                if old != record:
                    previous[record[key]] = record
                    changed[collection].append(record[key])
            payload[collection] = list(previous.values())
        if request.changes.dispute_type and request.changes.dispute_type != payload["dispute_type"]:
            payload["dispute_type"] = request.changes.dispute_type
            changed["dispute_type"] = [request.changes.dispute_type]
        if not any(changed.values()):
            return _error(case_id, version, "invalid_request", "变更与现有案件一致，没有可保存的新内容。")
        now = datetime.now(timezone.utc)
        payload.update(case_version=version + 1, updated_at=max(current.updated_at, now).isoformat())
        updated = CaseSnapshot.model_validate(payload)
        result = BusinessResult[UpdateCaseData](case_id=case_id, case_version=updated.case_version, status="ok",
            summary=f"案件已更新至版本 {updated.case_version}，保留旧版本及变更来源。",
            data=UpdateCaseData(case_snapshot=updated, changed_fact_ids=changed["facts"],
                changed_evidence_ids=changed["evidence"], changed_claim_ids=changed["claims"],
                changed_statement_ids=changed["statements"]))
        audit = CaseAuditRecord(case_id=case_id, case_version=updated.case_version,
            request_id=request.context.request_id, change_reason=request.change_reason, saved_at=now,
            changed_statement_ids=changed["statements"],
            changed_fact_ids=changed["facts"], changed_evidence_ids=changed["evidence"], changed_claim_ids=changed["claims"])
        # Recheck the deadline immediately before the atomic write.
        if request.context.deadline_at is not None and datetime.now(timezone.utc) >= request.context.deadline_at:
            return _error(case_id, version, "timeout", "更新请求已超过截止时间，未保存变更。")
        return repository.commit_update(updated, expected_case_version=version, audit=audit,
                                        request_digest=digest, result=result, deadline_at=request.context.deadline_at)
    except VersionConflict as exc:
        return _error(case_id, exc.actual, "version_conflict", str(exc),
                      {"expected_case_version": exc.expected, "actual_case_version": exc.actual})
    except CaseNotFound:
        return _error(case_id, version, "not_found", "案件不存在或不可读取。")
    except UpdateDeadlineExceeded:
        return _error(case_id, version, "timeout", "等待数据库事务期间请求已过期，未保存变更。")
    except ValidationError as exc:
        issues = [{"location": list(item["loc"]), "type": item["type"], "message": item["msg"]}
                  for item in exc.errors(include_input=False, include_context=False, include_url=False)]
        return _error(case_id, version, "invalid_request", "案件变更不合法。", {"issues": issues})
    except (ValueError, RequestConflict) as exc:
        return _error(case_id, version, "invalid_request", "案件变更不合法。", {"reason": str(exc).splitlines()[0]})
    except sqlite3.Error:
        return _error(case_id, version, "tool_error", "案件存储操作失败，未返回成功结果；请查询案件版本后重试。")


def _error(case_id, version, code, message, details=None):
    return BusinessResult[UpdateCaseData](case_id=case_id, case_version=version,
        status="error", summary=message, data=None,
        error=BusinessError(code=code, message=message, details=details))


def _validate_material_changes(request, repository, assets):
    case_id = request.context.case_id
    current=repository.get_case(case_id)
    existing={item.evidence_id for item in current.evidence}
    for evidence in request.changes.upsert_evidence:
        try:
            record,_=assets.get_file(case_id,evidence.file_id,evidence.file_version)
        except CaseNotFound:
            # Phase-two cases may predate the shared file store. Their
            # identities remain immutable; new evidence must be uploaded.
            if evidence.evidence_id in existing:
                continue
            raise ValueError("new evidence must reference an uploaded file in this case")
        for field in ("file_name","media_type","document_type","content_sha256"):
            if getattr(record,field)!=getattr(evidence,field):
                raise ValueError("evidence metadata differs from the uploaded file")
        for ref in evidence.material_refs:
            if ref.extraction_version:
                assets.read_extraction(case_id,ref)
    for fact in request.changes.upsert_facts:
        for ref in fact.material_refs:
            if ref.extraction_version:
                assets.read_extraction(case_id,ref)


def set_case_stage(repository, case_id, stage):
    """Save a trusted user route selection with version checking and audit."""
    current = repository.get_case(case_id)
    payload = current.model_dump(mode="json")
    payload.update(stage=stage, selected_route=stage if stage in ("negotiation", "mediation", "litigation") else None,
                   case_version=current.case_version + 1,
                   updated_at=max(datetime.now(timezone.utc), current.updated_at).isoformat())
    updated = CaseSnapshot.model_validate(payload)
    saved = repository.replace_case(updated, expected_case_version=current.case_version,
        audit=CaseAuditRecord(case_id=case_id, case_version=updated.case_version, request_id=None,
            change_reason="runtime_user_route_selection", saved_at=datetime.now(timezone.utc)))
    return saved.case_version


def record_user_statement(repository, *, case_id, expected_case_version, request_id, message_id, actor_id,
                          text, created_at):
    """Save an authenticated user statement so later records can quote it.

    It is stored as said and never interpreted as a confirmation. created_at is the
    application's original receipt time, reused on retries.
    """
    from .contracts import CaseChanges, UpdateCaseRequest
    from ..contracts import InvocationContext
    from ..models import UserStatement
    return update_case(UpdateCaseRequest(
        context=InvocationContext(request_id=request_id, case_id=case_id, experiment_run_id=None, deadline_at=None),
        expected_case_version=expected_case_version,
        changes=CaseChanges(upsert_statements=[UserStatement(message_id=message_id, actor_id=actor_id,
                                                             text=text, created_at=created_at)]),
        change_reason="application_recorded_user_reply"), repository)
