"""search_statutes adapter: fake responses for the mapping, the real index when it has been built."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from backend.agent.case_port import InMemoryCaseStore
from backend.agent.llm import ScriptedLLM, tool_call
from backend.agent.runner import AgentRunner
from backend.agent.store import RunStore
from backend.common.schemas import ToolContext
from backend.knowledge.config import keyword_index_path
from backend.knowledge.retrieval.results import no_results, to_result
from backend.tools.knowledge_tools import _default_search, register_knowledge_tools, to_tool_result
from backend.tools.registry import ToolRegistry

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
                       deadline=datetime.now(timezone.utc), stage="consultation")


def registry_with(search) -> ToolRegistry:
    reg = ToolRegistry()
    register_knowledge_tools(reg, search)
    return reg


def test_ok_response_maps_to_source_refs():
    result = to_tool_result(ok_response())
    assert result.status == "ok" and "第一千二百一十三条" in result.summary
    ref = result.source_refs[0]
    assert (ref.source_id, ref.source_type, ref.title, ref.locator, ref.version) == (
        PROVISION["provision_id"], "statute", "中华人民共和国民法典", "第一千二百一十三条", "2021-01-01")
    assert ref.excerpt == PROVISION["text"] and ref.url == "https://flk.npc.gov.cn/"
    hit = result.data["hits"][0]
    assert (hit["rank"], hit["page_start"], hit["version_warning"]) == (1, 200, None)


def test_no_results_is_empty_not_error_and_keeps_reason():
    result = to_tool_result(no_results("民法典第九千条", "exact_article_not_found", "当前语料未找到"))
    assert result.status == "empty" and result.error is None
    assert result.data["reason"] == "exact_article_not_found" and "未找到" in result.summary
    assert result.source_refs == []


def test_incident_before_effective_date_is_flagged():
    result = to_tool_result(ok_response(), incident_date="2020-06-01")
    assert result.status == "ok"
    assert "2021-01-01" in result.data["hits"][0]["version_warning"]
    assert "version_warning" in result.summary


def test_incident_after_expiry_is_flagged_and_in_force_is_not():
    expired = {**PROVISION, "expiry_date": "2024-01-01", "status": "失效"}
    assert "失效" in to_tool_result(ok_response(expired), "2024-06-01").data["hits"][0]["version_warning"]
    assert to_tool_result(ok_response(expired), "2023-06-01").data["hits"][0]["version_warning"] is None
    assert to_tool_result(ok_response(), "2026-05-12").data["hits"][0]["version_warning"] is None


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


needs_index = pytest.mark.skipif(not keyword_index_path().exists(),
                                 reason="run scripts/rag/build_bm25_index.py first")


@pytest.fixture(scope="module")
def real_search():
    return _default_search()


@needs_index
def test_real_exact_article_lookup(real_search):
    result = registry_with(real_search).execute(
        "search_statutes", {"query": "民法典第一千二百一十三条"}, ctx()).result
    assert result.status == "ok" and len(result.source_refs) == 1
    assert result.source_refs[0].locator == "第一千二百一十三条"
    assert "强制保险" in result.source_refs[0].excerpt
    assert result.data["hits"][0]["matched_by"] == ["exact"]


@needs_index
def test_real_article_outside_corpus_returns_empty(real_search):
    result = registry_with(real_search).execute("search_statutes", {"query": "民法典第九千条"}, ctx()).result
    assert result.status == "empty" and result.data["reason"] == "exact_article_not_found"


@needs_index
def test_real_retrieval_through_the_agent_loop(real_search, tmp_path):
    cases = InMemoryCaseStore()
    cases.create(CASE, {"facts": {}})
    reg = registry_with(real_search)

    def cite_first_hit(messages, tools):
        result = json.loads([m for m in messages if m["role"] == "tool"][-1]["content"])
        source_id = result["source_refs"][0]["source_id"]
        return tool_call("finish", answer="依据检索到的条文作答。",
                         citations=[{"source_id": source_id, "location": "依据检索到的条文作答。"}])

    llm = ScriptedLLM([tool_call("search_statutes", query="机动车交通事故 交强险 赔偿顺序", top_k=3), cite_first_hit])
    runner = AgentRunner(llm, reg, cases, RunStore(tmp_path))
    info = runner.start_run(CASE, "交强险怎么赔？")

    assert info.status == "completed"
    titles = {(s.title, s.locator) for s in info.final.source_refs}
    assert ("中华人民共和国民法典", "第一千二百一十三条") in titles
    assert info.final.citations and not info.final.open_issues  # the cited source was really returned
    step = next(s for s in runner.get_trace(info.run_id) if s.tool_name == "search_statutes")
    assert runner.get_step_result(info.run_id, step.result_ref)["result"]["data"]["hits"]
