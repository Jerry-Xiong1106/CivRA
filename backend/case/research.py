"""Member 3 research preparation and exact-source organization; no retrieval or LLM decisions."""

from collections import defaultdict
from datetime import date, datetime, timezone
import hashlib
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, ValidationError, model_validator

from .contracts import CaseRequest
from .models import BusinessError, BusinessResult, ContractModel, Identifier, SourcePointer
from .repository import CaseNotFound, VersionConflict
from .evidence import UNKNOWN_TEXT


RESEARCH_VERSION = "case-research-0.1.0"
DISPUTES = {"traffic_accident": "交通事故民事赔偿", "private_lending": "民间借贷", "loan": "民间借贷", "lending": "民间借贷",
            "sales_contract": "买卖合同", "leasing": "房屋租赁"}
CLAIMS = {"repair_cost": "维修费用", "medical_cost": "医疗费用", "towing_cost": "拖车费用", "appraisal_cost": "鉴定费用"}
EVENT_FIELDS = {"accident_date", "loan_date", "contract_date", "delivery_date"}


class ResearchRequest(CaseRequest):
    topics: list[Literal["liability", "losses", "procedure", "similar_cases"]] = Field(
        default_factory=lambda: ["liability", "losses", "similar_cases"], min_length=1, max_length=4)
    knowledge_version: Identifier | None = None
    as_of_date: date | None = None
    top_k: int = Field(default=5, ge=1, le=20, strict=True)

    @model_validator(mode="after")
    def distinct_topics(self):
        if len(self.topics) != len(set(self.topics)):
            raise ValueError("topics must be unique")
        return self


class ResearchQuery(ContractModel):
    query_id: str
    tool: Literal["search_statutes", "search_cases"]
    query: str
    filters: dict
    top_k: int
    knowledge_version: str | None
    fact_ids: list[str]
    claim_ids: list[str]


class ResearchPlan(ContractModel):
    implementation_version: str = RESEARCH_VERSION
    queries: list[ResearchQuery]
    confirmed_context: list[dict]
    unresolved_items: list[str]
    limitations: list[str]


class SourceSelection(ContractModel):
    source_ref: SourcePointer
    expected_kind: Literal["statute", "case"]
    quote: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def pinned_version(self):
        if self.source_ref.source_version is None:
            raise ValueError("source selection requires an explicit source version")
        return self


class SourceReviewRequest(CaseRequest):
    selections: list[SourceSelection] = Field(max_length=30)
    reference_date: date | None = None


class SourceCard(ContractModel):
    source_ref: SourcePointer
    source_type: Literal["statute", "case"]
    title: str
    locator: str | dict
    url: str | None
    quote: str
    quote_start: int
    quote_end: int
    text_sha256: str
    metadata: dict
    warnings: list[str]


class SourceReviewData(ContractModel):
    statutes: list[SourceCard]
    cases: list[SourceCard]
    rejected: list[dict]
    limitations: list[str]


class CaseResearchService:
    def __init__(self, repository, *, source_resolver=None):
        self.repository, self.source_resolver = repository, source_resolver

    @staticmethod
    def _deadline(request):
        if request.context.deadline_at and datetime.now(timezone.utc) >= request.context.deadline_at:
            raise TimeoutError("research request deadline expired")

    def _read(self, request):
        self._deadline(request)
        snapshot = self.repository.get_case(request.context.case_id)
        if snapshot.case_version != request.case_version:
            raise VersionConflict(request.case_version, snapshot.case_version)
        return snapshot

    def prepare_queries(self, request):
        case_id, version = None, None
        try:
            request = ResearchRequest.model_validate(request.model_dump(mode="json") if isinstance(request, ResearchRequest) else request)
            case_id, version = request.context.case_id, request.case_version
            snapshot = self._read(request)
            evidence = {item.evidence_id: item for item in snapshot.evidence}
            active = [fact for fact in snapshot.facts if not fact.superseded_by and
                      (not fact.evidence_ids or any(evidence[key].status != "excluded" for key in fact.evidence_ids))]
            by_field = defaultdict(list)
            for fact in active:
                by_field[fact.field].append(fact)
            unresolved, context, event_dates = [], [], set()
            for field in sorted(EVENT_FIELDS | {"insurance", "liability_finding"}):
                values = by_field[field]
                known = [fact for fact in values if fact.value is not None and not
                         (fact.value_type == "text" and fact.value.strip().lower() in UNKNOWN_TEXT)]
                if len({str(fact.value) for fact in known}) > 1:
                    unresolved.append(f"{field}: 存在不同记载，未选取其中一个作为检索前提")
                    continue
                confirmed = [fact for fact in known if fact.confirmation_status == "confirmed"]
                context.extend({"fact_id": fact.fact_id, "field": field, "value": fact.value,
                                "confirmation_status": "confirmed"} for fact in confirmed)
                if field in EVENT_FIELDS:
                    event_dates.update(fact.value for fact in confirmed if fact.value_type == "date")
            incident_date = next(iter(event_dates)) if len(event_dates) == 1 else None
            if incident_date is None:
                unresolved.append("incident_date: 尚无唯一、已确认的事件日期，检索日期过滤保持空值")
            if request.knowledge_version is None:
                unresolved.append("knowledge_version: 成员二尚需提供明确知识库快照版本")
            if request.as_of_date is None:
                unresolved.append("as_of_date: 本次检索截止日期尚未明确")
            domain = DISPUTES.get(snapshot.dispute_type)
            if domain is None:
                unresolved.append("dispute_type: 尚未配置对应查询词，使用通用民事检索词")
                domain = "民事纠纷"
            claims = [claim for claim in snapshot.claims if claim.status != "withdrawn"]
            loss_terms = " ".join(sorted({CLAIMS[claim.kind] for claim in claims if claim.kind in CLAIMS}))
            if any(claim.kind not in CLAIMS for claim in claims):
                unresolved.append("claim.kind: 有诉求未配置专门查询词，保留原诉求ID供成员四组织查询")
            topics = {"liability": "责任承担 事实前提 适用条件", "losses": (loss_terms or "请求项目")+" 范围 证据要求",
                      "procedure": "程序条件 管辖 办理步骤", "similar_cases": (loss_terms or "争议")+" 相似案例 事实差异 裁判理由"}
            queries = []
            for topic in request.topics:
                tool = "search_cases" if topic == "similar_cases" else "search_statutes"
                query = domain + " " + topics[topic]
                identity = hashlib.sha256(f"{case_id}:{version}:{topic}:{query}".encode()).hexdigest()[:16]
                queries.append(ResearchQuery(query_id="rq_"+identity, tool=tool, query=query,
                    filters={"domain": snapshot.dispute_type, "jurisdiction": snapshot.jurisdiction,
                             "incident_date": incident_date, "as_of_date": request.as_of_date.isoformat() if request.as_of_date else None},
                    top_k=request.top_k, knowledge_version=request.knowledge_version,
                    fact_ids=[item["fact_id"] for item in context], claim_ids=[claim.claim_id for claim in claims]))
            self._deadline(request)
            return BusinessResult[ResearchPlan](case_id=case_id, case_version=version,
                status="insufficient" if unresolved else "ok", summary="已准备检索请求；尚未执行真实检索或判断法律适用。",
                data=ResearchPlan(queries=queries, confirmed_context=context, unresolved_items=unresolved,
                    limitations=["查询词是业务检索输入，不是法律结论；成员四决定是否采用、修改或追加检索。",
                                 "未向查询文字插入姓名、联系方式、原始用户陈述或未确认事实；不自动调用网络/模型。",
                                 "事件日期和检索截止日期分开传递；实际时间过滤语义须与成员二对齐。",
                                 "候选检索接口沿用成员五草案字段，未改写成员二检索实现或全局工具注册。"] ))
        except (ValidationError, ValueError, CaseNotFound, TimeoutError) as exc:
            return self._error(case_id, version, exc)

    def review_sources(self, request):
        case_id, version = None, None
        try:
            request = SourceReviewRequest.model_validate(request.model_dump(mode="json") if isinstance(request, SourceReviewRequest) else request)
            case_id, version = request.context.case_id, request.case_version
            self._read(request)
            statutes, cases, rejected, refs, seen = [], [], [], [], set()
            for selection in request.selections:
                self._deadline(request)
                pointer = selection.source_ref
                key = (pointer.source_id, pointer.source_version, selection.expected_kind, selection.quote)
                if key in seen:
                    continue
                seen.add(key)
                if self.source_resolver is None:
                    rejected.append({"source_ref": pointer.model_dump(), "reason": "未接入精确版本原文读取器"})
                    continue
                try:
                    raw = dict(self.source_resolver(pointer.model_copy(deep=True)))
                    self._deadline(request)
                    if raw.get("source_id") != pointer.source_id or raw.get("version") != pointer.source_version:
                        raise ValueError("来源ID或版本不一致，拒绝自动换成最新版本")
                    text, title, locator = raw.get("text"), raw.get("title"), raw.get("locator")
                    if not isinstance(text, str) or not text.strip() or len(text) > 500000:
                        raise ValueError("缺少可核对原文或超过单来源长度上限")
                    locator_valid = isinstance(locator, str) and bool(locator.strip())
                    if isinstance(locator, dict):
                        locator_valid = any(isinstance(locator.get(key), str) and bool(locator[key].strip())
                                            for key in ("article", "paragraph", "chunk_id")) or (
                                            type(locator.get("page")) is int and locator["page"] >= 1)
                    if not isinstance(title, str) or not title.strip() or not locator_valid:
                        raise ValueError("缺少有效标题或定位")
                    kind = "statute" if raw.get("source_type") in {"law", "regulation", "statute", "judicial_interpretation"} else raw.get("source_type")
                    if kind != selection.expected_kind:
                        raise ValueError("来源类型与预期不符")
                    url = raw.get("url")
                    if url is not None and (not isinstance(url, str) or urlparse(url).scheme not in {"http", "https"} or not urlparse(url).netloc):
                        raise ValueError("来源链接格式不合法")
                    quote = selection.quote if selection.quote is not None else text[:400]
                    start = text.find(quote)
                    if start < 0:
                        raise ValueError("请求引文不是该版本原文的连续片段")
                    metadata, warnings = {}, []
                    if text.find(quote, start+1) >= 0:
                        warnings.append("同一引文在返回原文中出现多次，当前位置为首次匹配；请进一步明确条款或段落定位。")
                    for field in ("effective_from", "effective_to", "decision_date", "verified_on", "court", "jurisdiction", "dispute_type", "text_scope"):
                        value = raw.get(field)
                        if value is not None and not isinstance(value, str):
                            raise ValueError("来源元数据字段必须是字符串或空值")
                        if value is not None and field in {"effective_from", "effective_to", "decision_date", "verified_on"}:
                            date.fromisoformat(value)
                        metadata[field] = value
                    if metadata["text_scope"] == "short_excerpt_only":
                        warnings.append("读取器仅提供短引文；完整条款及全部适用条件须从官方来源或成员二读取。")
                    for field in ("source_file", "page_start", "page_end", "publication_date", "registered_status"):
                        if field in raw:
                            metadata[field] = raw[field]
                    if "page_start" in raw or "page_end" in raw:
                        if type(raw.get("page_start")) is not int or type(raw.get("page_end")) is not int or not 1 <= raw["page_start"] <= raw["page_end"]:
                            raise ValueError("PDF页码范围不合法")
                    if url and urlparse(url).path in {"", "/"}:
                        warnings.append("来源链接为站点首页，请使用所记录的PDF文件和页码定位原文。")
                    if kind == "statute" and metadata["effective_from"] is None:
                        warnings.append("缺少生效日期元数据，法律适用时点待核实。")
                    if kind == "case" and (not metadata["court"] or not metadata["decision_date"]):
                        warnings.append("缺少法院或裁判日期，不补造类案信息。")
                    reference = request.reference_date
                    if reference and kind == "statute":
                        if metadata["effective_from"] and reference < date.fromisoformat(metadata["effective_from"]):
                            warnings.append("参考日期早于来源生效日期，不能直接作为该时点适用依据。")
                        if metadata["effective_to"] and reference >= date.fromisoformat(metadata["effective_to"]):
                            warnings.append("参考日期达到或晚于记录的效力结束日期，需核实边界与过渡规则。")
                        if metadata["verified_on"] and reference > date.fromisoformat(metadata["verified_on"]):
                            warnings.append("参考日期晚于本地核对日期，需重新核实来源状态。")
                    card = SourceCard(source_ref=pointer, source_type=kind, title=title, locator=locator, url=url,
                        quote=quote, quote_start=start, quote_end=start+len(quote),
                        text_sha256=hashlib.sha256(text.encode()).hexdigest(), metadata=metadata, warnings=warnings)
                    (statutes if kind == "statute" else cases).append(card)
                    if pointer not in refs:
                        refs.append(pointer)
                except TimeoutError:
                    raise
                except Exception as exc:
                    rejected.append({"source_ref": pointer.model_dump(), "reason": f"{type(exc).__name__}: {str(exc)[:180]}"})
            versions = defaultdict(set)
            for card in statutes+cases:
                versions[card.source_ref.source_id].add(card.source_ref.source_version)
            for card in statutes+cases:
                if len(versions[card.source_ref.source_id]) > 1:
                    card.warnings.append("同一来源存在多个选定版本，需明确适用时点；未自动合并或选最新版。")
            self._deadline(request)
            status = "empty" if not request.selections else "insufficient" if rejected or any(card.warnings for card in statutes+cases) else "ok"
            return BusinessResult[SourceReviewData](case_id=case_id, case_version=version, status=status,
                summary=f"整理{len(statutes)}项法条/规则来源、{len(cases)}项案例来源，{len(rejected)}项未通过定位核对。",
                data=SourceReviewData(statutes=statutes, cases=cases, rejected=rejected,
                    limitations=["只核对来源身份、版本、原文片段和已提供元数据；未判断引文是否支持法律结论。",
                                 "案例相似性、事实差异、法律适用及不利材料分析仍由成员二/四提供并独立核验。",
                                 "片段位置针对读取器返回的text；如读取器只允许摘录，定位范围也仅为该摘录。"]), source_refs=refs)
        except (ValidationError, ValueError, CaseNotFound, TimeoutError) as exc:
            return self._error(case_id, version, exc)

    @staticmethod
    def _error(case_id, version, exc):
        code = "version_conflict" if isinstance(exc, VersionConflict) else "not_found" if isinstance(exc, CaseNotFound) else "timeout" if isinstance(exc, TimeoutError) else "invalid_request"
        if isinstance(exc, VersionConflict):
            version = exc.actual
        return BusinessResult(case_id=case_id, case_version=version, status="error", data=None,
            summary="检索业务请求未完成。", error=BusinessError(code=code, message=str(exc).splitlines()[0]))
