"""Side-effect-free answer review for Runtime and evaluation integration."""
import hashlib
import re

from pydantic import Field, StrictBool

from .contracts import CaseRequest, VerifyOutputRequest
from .models import BusinessOutput, ContractModel, Finding, OutputLink, SourcePointer
from .repository import CaseNotFound, VersionConflict


class AnswerSpan(ContractModel):
    start: int = Field(ge=0, strict=True)
    end: int = Field(ge=1, strict=True)


class AnswerCitation(AnswerSpan):
    source_ref: SourcePointer
    source_quote: str | None = Field(default=None, min_length=1, max_length=2000)


class AnswerReviewRequest(CaseRequest):
    answer: str = Field(min_length=1, max_length=100000)
    # Supplied by application/evaluation from actual retrieval records, not invented by a model.
    retrieved_sources: list[SourcePointer] = Field(default_factory=list, max_length=50)
    citations: list[AnswerCitation] = Field(default_factory=list, max_length=200)
    required_citation_spans: list[AnswerSpan] = Field(default_factory=list, max_length=200)
    requires_citations: StrictBool = True


def audit_citations(output, available_sources, resolved_sources):
    """Check identifiers and supplied coverage ranges, never infer semantic support."""
    data = output.structured_data or {}
    if "answer_citation_policy" not in data:
        return [], None
    policy = AnswerReviewRequest.model_validate(data["answer_citation_policy"])
    answer = output.text or ""
    issues = []

    def add(message, location="answer"):
        identity = hashlib.sha256((message + location).encode()).hexdigest()[:20]
        issues.append(Finding(finding_id="citation_" + identity, category="citation", severity="warning",
                              message=message, output_location=location,
                              suggested_correction="核对原始回答、检索记录及完整原文；修改后重新核验，不事后伪造来源。"))

    if answer != policy.answer:
        add("引用检查所绑定的回答与待核验正文不一致。")
        return issues, {"policy_matches_answer": False, "semantic_support_checked": False}
    allowed = {(r.source_id, r.source_version) for r in available_sources}
    allowed &= {(r.source_id, r.source_version) for r in policy.retrieved_sources}
    located = {(r["source_id"], r["version"]): r for r in resolved_sources}
    valid = []
    seen = set()
    for citation in policy.citations:
        key = (citation.source_ref.source_id, citation.source_ref.source_version)
        location = f"answer:{citation.start}:{citation.end}"
        identity = (*key, citation.start, citation.end)
        if identity in seen:
            add("同一回答位置重复登记同一来源。", location)
            continue
        seen.add(identity)
        if not 0 <= citation.start < citation.end <= len(answer):
            add("引用位置超出回答正文。", location)
        elif key not in allowed or key not in located:
            add("引用未出现在本次实际候选中，或不能读取同版本原文。", location)
        elif citation.source_quote is not None and citation.source_quote not in located[key]["text"]:
            add("声明的逐字引文不是该版本原文的连续片段。", location)
        else:
            valid.append(citation)
    known_ids = {key[0] for key in allowed}
    bound_ids = {c.source_ref.source_id for c in valid}
    marker_count = 0
    for match in re.finditer(r"\[([^\[\]\r\n]{1,300})\]", answer):
        identity = match.group(1)
        if identity in known_ids or "::" in identity or identity.startswith(("source_", "source:", "src_")):
            marker_count += 1
            if identity not in known_ids:
                add("正文使用了未返回的来源ID。", f"answer:{match.start()}:{match.end()}")
            elif identity not in bound_ids:
                add("正文来源标记缺少有效的结论位置绑定。", f"answer:{match.start()}:{match.end()}")
    if policy.requires_citations and not valid:
        add("需要法律依据的回答没有有效的结论—来源绑定。")
    covered = 0
    for span in policy.required_citation_spans:
        if not 0 <= span.start < span.end <= len(answer):
            add("待检查法律主张的位置超出正文。")
        elif any(c.start <= span.start and c.end >= span.end for c in valid):
            covered += 1
        else:
            add("指定法律主张缺少有效引用。", f"answer:{span.start}:{span.end}")
    return issues, {"policy_matches_answer": True, "citation_count": len(policy.citations),
                    "valid_citation_count": len(valid), "inline_marker_count": marker_count,
                    "required_span_count": len(policy.required_citation_spans), "covered_span_count": covered,
                    "semantic_support_checked": False,
                    "coverage_scope": "仅覆盖调用方明确给出的主张区间；完整法律主张识别及支持关系需语义复核。"}


def review_answer(service, request):
    from .business_support import deadline, digest, failure
    case_id, version = None, None
    try:
        request = AnswerReviewRequest.model_validate(request)
        case_id, version = request.context.case_id, request.case_version
        deadline(request)
        case = service.repository.get_case(case_id)
        if case.case_version != version:
            raise VersionConflict(version, case.case_version)
        output = BusinessOutput(output_id="answer_review_" + digest(request)[:24], output_version=1,
            case_id=case_id, case_version=version, output_type="analysis", review_status="needs_review",
            text=request.answer, source_refs=request.retrieved_sources,
            links=[OutputLink(output_location=f"answer:{c.start}:{c.end}", source_refs=[c.source_ref]) for c in request.citations],
            structured_data={"answer_citation_policy": request.model_dump(mode="json")})
        return service.verifier.verify_output(VerifyOutputRequest(context=request.context, case_version=version,
            case_snapshot=case, candidate_output=output, cited_sources=request.retrieved_sources))
    except (ValueError, TypeError, CaseNotFound, TimeoutError, VersionConflict) as exc:
        return failure(case_id, version, exc)
