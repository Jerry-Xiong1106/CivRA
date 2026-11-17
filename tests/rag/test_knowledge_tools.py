"""search_statutes adapter: fake responses for the mapping, the real index when it has been built."""

from __future__ import annotations
from backend.common.schemas import RunConfig

import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.agent.case_port import InMemoryCaseStore
from backend.agent.model.llm import ScriptedLLM, tool_call
from backend.agent.runner import AgentRunner
from backend.agent.runtime.store import RunStore
from backend.common.schemas import SourceLocator, SourceRef, ToolContext, ToolResult
from backend.knowledge.config import RetrievalConfig, keyword_index_path
from backend.knowledge.retrieval.service import RetrievalService
from backend.knowledge.retrieval.results import no_results, to_result
from tests.native_tools import register_knowledge_tools
from backend.tools.knowledge_tools import to_tool_result
from tests.native_tools import NativeTools

CASE = "case_real"
PROVISION = {
    "provision_id": "中华人民共和国民法典::2020-05-28::第一千二百一十三条",
    "law_title": "中华人民共和国民法典", "article_no": "第一千二百一十三条",
    "hierarchy_path": ["第七编 侵权责任", "第五章 机动车交通事故责任"], "text": "机动车发生交通事故造成损害……",
    "source_url": "https://flk.npc.gov.cn/", "source_file": "民法典.pdf", "page_start": 200, "page_end": 200,
    "publication_date": "2020-05-28", "effective_date": "2021-01-01", "expiry_date": None, "status": "有效",
}


def ok_response(provision=PROVISION):
    return {"status": "ok", "query": "q", "results": [to_result(provision, 1, 8.5, ["keyword"])], "warnings": []}


def ctx():
    return ToolContext(case_id=CASE, run_id="r", step_id=1, case_version=1,
                       deadline=datetime.now(timezone.utc) + timedelta(seconds=30), stage="consultation")


def registry_with(search) -> NativeTools:
    reg = NativeTools()
    register_knowledge_tools(reg, search)
    return reg


def test_ok_response_has_one_reference_per_match_and_one_copy_of_text():
    result = to_tool_result(ok_response())
    assert result.status == "ok" and "1 条候选" in result.summary
    ref = SourceRef.model_validate(result.data["matches"][0]["source_ref"])
    assert (ref.source_id, ref.source_type, ref.title, ref.locator.article, ref.source_version) == (
        PROVISION["provision_id"], "statute", "中华人民共和国民法典", "第一千二百一十三条", "2020-05-28")
    assert ref.effective_from == "2021-01-01"
    assert ref.url == "https://flk.npc.gov.cn/"
    assert "version" not in SourceRef.model_fields and "excerpt" not in SourceRef.model_fields
    assert "source_refs" not in result.model_dump()
    assert result.model_dump_json().count(PROVISION["text"]) == 1
    assert result.model_dump_json().count('"source_id"') == 1
    hit = result.data["matches"][0]
    assert "version" not in hit["source_ref"] and "excerpt" not in hit["source_ref"]
    assert not {"rank", "score", "score_type"}.intersection(hit["retrieval"])
    assert (hit["rank"], hit["source_ref"]["locator"]["page_start"], hit["score_type"]) == (1, 200, "bm25")


def test_no_results_is_empty_not_error_and_keeps_reason():
    result = to_tool_result(no_results("民法典第九千条", "exact_article_not_found", "当前语料未找到"))
    assert result.status == "empty" and result.error is None
    assert result.data["empty_reason"] == "exact_article_not_found" and "未找到" in result.summary
    assert result.data["matches"] == [] and "source_refs" not in result.model_dump()


def test_unimplemented_date_parameter_is_rejected_before_search():
    calls = []
    reg = registry_with(lambda *args: calls.append(args))
    result = reg.execute("search_statutes", {"query": "q", "incident_date": "2020-06-01"}, ctx())
    assert result.result.error.code == "INVALID_ARGS" and result.attempts == 0
    assert calls == []


def test_structured_reference_roundtrip_and_unknown_provenance():
    result = to_tool_result(ok_response())
    restored = ToolResult.model_validate_json(result.model_dump_json())
    ref = SourceRef.model_validate(restored.data["matches"][0]["source_ref"])
    assert isinstance(ref.locator, SourceLocator)
    assert ref.locator.page_end == 200
    assert ref.retrieved_at is None
    assert restored.data["knowledge_version"] is None


def test_registered_tool_passes_arguments_and_validates():
    calls = []

    def search(query, top_k=None):
        calls.append((query, top_k))
        return ok_response()

    reg = registry_with(search)
    assert reg.execute("search_statutes", {"query": "交强险", "top_k": 3}, ctx()).result.status == "ok"
    assert calls == [("交强险", 3)]
    assert reg.execute("search_statutes", {"query": "x", "dispute_type": "t"}, ctx()).result.error.code == "INVALID_ARGS"
    assert reg.execute("search_statutes", {"query": "x", "incident_date": "2026/5/12"}, ctx()).result.error.code == "INVALID_ARGS"
    assert len(calls) == 1
    assert reg.get_spec("search_statutes").feedback_loop == "retrieve_verify"


def test_missing_index_is_reported_with_how_to_fix():
    def search(query, top_k=None):
        raise FileNotFoundError("data/index/bm25.pkl")

    error = registry_with(search).execute("search_statutes", {"query": "q"}, ctx()).result.error
    assert error.code == "UPSTREAM" and "build_bm25_index" in error.message


def test_obsolete_top_level_source_refs_are_rejected():
    with pytest.raises(ValueError, match="source_refs"):
        ToolResult(status="ok", source_refs=[])


def test_source_ref_rejects_old_fields_and_string_locator():
    ref = to_tool_result(ok_response()).data["matches"][0]["source_ref"]
    for update in ({"version": "2021-01-01"}, {"excerpt": "old quote"},
                   {"locator": "第一条"}, {"source_version": None}):
        with pytest.raises(ValueError):
            SourceRef.model_validate({**ref, **update})


def test_invalid_source_is_not_sent_to_model_or_collected(tmp_path):
    bad = {**PROVISION, "page_start": 0}
    reg = registry_with(lambda query, top_k=None: ok_response(bad))
    def check_error(messages, tools):
        result = json.loads(messages[-1]["content"])
        assert result["error"]["code"] == "INTERNAL" and result.get("data") is None
        return tool_call("finish", answer="来源有误，需要核实。")
    cases = InMemoryCaseStore()
    cases.create(CASE, {})
    runner = AgentRunner(ScriptedLLM([tool_call("search_statutes", query="q"), check_error]),
                         reg, cases, RunStore(tmp_path), default_config=RunConfig(conversation_route="case_work"))
    info = runner.start_run(CASE, "test")
    assert info.status == "completed" and info.final.source_refs == []


needs_index = pytest.mark.skipif(not keyword_index_path().exists(),
                                 reason="run scripts/rag/build_bm25_index.py first")


@pytest.fixture(scope="module")
def real_search():
    # Offline integration uses keyword explicitly; production defaults to exact-vector.
    return RetrievalService.from_config(RetrievalConfig(channel="keyword")).search


@needs_index
def test_real_exact_article_lookup(real_search):
    result = registry_with(real_search).execute(
        "search_statutes", {"query": "民法典第一千二百一十三条"}, ctx()).result
    assert result.status == "ok" and len(result.data["matches"]) == 1
    assert result.data["matches"][0]["source_ref"]["locator"]["article"] == "第一千二百一十三条"
    assert "强制保险" in result.data["matches"][0]["text"]
    assert result.data["matches"][0]["retrieval"]["matched_by"] == ["exact"]


@needs_index
def test_real_article_outside_corpus_returns_empty(real_search):
    result = registry_with(real_search).execute("search_statutes", {"query": "民法典第九千条"}, ctx()).result
    assert result.status == "empty" and result.data["empty_reason"] == "exact_article_not_found"


@needs_index
def test_real_retrieval_through_the_agent_loop(real_search, tmp_path):
    cases = InMemoryCaseStore()
    cases.create(CASE, {"facts": {}})
    reg = registry_with(real_search)

    def cite_first_hit(messages, tools):
        result = json.loads([m for m in messages if m["role"] == "tool"][-1]["content"])
        source_id = result["data"]["matches"][0]["source_ref"]["source_id"]
        return tool_call("finish", answer="依据检索到的条文作答。",
                         citations=[{"source_id": source_id, "location": "依据检索到的条文作答。"}])

    llm = ScriptedLLM([tool_call("search_statutes", query="机动车交通事故 交强险 赔偿顺序", top_k=3), cite_first_hit])
    runner = AgentRunner(llm, reg, cases, RunStore(tmp_path), default_config=RunConfig(conversation_route="case_work"))
    info = runner.start_run(CASE, "交强险怎么赔？")

    assert info.status == "completed"
    titles = {(s.title, s.locator.article) for s in info.final.source_refs}
    assert ("中华人民共和国民法典", "第一千二百一十三条") in titles
    assert info.final.citations and not info.final.open_issues  # the cited source was really returned
    step = next(s for s in runner.get_trace(info.run_id) if s.tool_name == "search_statutes")
    assert runner.get_step_result(info.run_id, step.result_ref)["result"]["data"]["matches"]
