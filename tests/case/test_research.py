"""Business research preparation and source integrity; fixtures are not real case law."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backend.case import CaseService, SQLiteCaseRepository, load_development_case
from backend.case.models import CaseSnapshot
from backend.case.research.sources import CaseResearchService


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = SQLiteCaseRepository(Path(self.temp.name)/"cases.db")
        self.case = self.repo.create_case(load_development_case())
        self.service = CaseService(self.repo)

    def request(self, **extra):
        return {"context": {"request_id": "research", "case_id": self.case.case_id,
                            "experiment_run_id": None, "deadline_at": None}, "case_version": 1, **extra}

    def source(self, kind="statute", version="v1"):
        return {"source_id": "synthetic-source", "version": version, "source_type": kind,
                "title": "合成来源，仅开发测试", "locator": {"article": "合成第一条"}, "url": None,
                "text": "合成条文前段。用于校验原文片段与来源版本。合成后段。", "effective_from": "2020-01-01",
                "effective_to": None, "court": "合成法院" if kind == "case" else None,
                "decision_date": "2025-01-01" if kind == "case" else None}

    def selection(self, kind="statute", version="v1", quote=None):
        value = {"source_ref": {"source_id": "synthetic-source", "source_version": version}, "expected_kind": kind}
        if quote is not None:
            value["quote"] = quote
        return value

    def review(self, source=None, selections=None, **extra):
        source = source or self.source()
        service = CaseResearchService(self.repo, source_resolver=lambda pointer: source)
        return service.review_sources(self.request(selections=selections or [self.selection()], **extra))

    def test_query_plan_preserves_unknown_conflicting_dates_and_snapshot(self):
        before = self.case.model_dump()
        result = self.service.prepare_research(self.request())
        self.assertEqual(result.status, "insufficient")
        self.assertEqual(len(result.data.queries), 3)
        self.assertTrue(all(query.filters["incident_date"] is None for query in result.data.queries))
        self.assertTrue(any("accident_date" in issue for issue in result.data.unresolved_items))
        self.assertTrue(any("knowledge_version" in issue for issue in result.data.unresolved_items))
        self.assertEqual(self.repo.get_case(self.case.case_id).model_dump(), before)

    def test_queries_do_not_include_private_name_or_statement(self):
        result = self.service.prepare_research(self.request(knowledge_version="synthetic-k1", as_of_date="2026-11-09"))
        text = " ".join(query.query for query in result.data.queries)
        self.assertNotIn("示例甲", text)
        self.assertNotIn(self.case.statements[0].text, text)
        self.assertIn("维修费用", text)
        self.assertNotIn("claimant_name", str(result.data.confirmed_context))

    def test_confirmed_unique_date_is_used_without_assuming_latest(self):
        payload = self.case.model_dump(mode="json")
        payload["case_id"] = "unique_date"
        payload["facts"][0]["confirmation_status"] = "confirmed"
        payload["facts"][0]["confirmation"] = payload["facts"][2]["confirmation"]
        payload["facts"][1]["superseded_by"] = "f_date_user"
        case = self.repo.create_case(CaseSnapshot.model_validate(payload))
        request = self.request(knowledge_version="synthetic-k1", as_of_date="2026-11-09")
        request["context"]["case_id"] = case.case_id
        result = self.service.prepare_research(request)
        self.assertEqual(result.status, "ok")
        self.assertTrue(all(query.filters["incident_date"] == "2026-05-12" for query in result.data.queries))
        self.assertTrue(all(query.knowledge_version == "synthetic-k1" for query in result.data.queries))

    def test_unknown_dispute_does_not_fabricate_domain_rules(self):
        payload = self.case.model_dump(mode="json")
        payload.update(case_id="unknown_type", dispute_type="unconfigured")
        case = self.repo.create_case(CaseSnapshot.model_validate(payload))
        request = self.request()
        request["context"]["case_id"] = case.case_id
        result = self.service.prepare_research(request)
        self.assertTrue(all(query.query.startswith("民事纠纷") for query in result.data.queries))
        self.assertTrue(any("dispute_type" in issue for issue in result.data.unresolved_items))

    def test_invalid_query_topic_top_k_and_stale_version(self):
        for fields in [{"topics": ["losses", "losses"]}, {"topics": ["invent_law"]}, {"top_k": 0}, {"top_k": True}]:
            with self.subTest(fields=fields):
                self.assertEqual(self.service.prepare_research(self.request(**fields)).error.code, "invalid_request")
        request = self.request()
        request["case_version"] = 2
        self.assertEqual(self.service.prepare_research(request).error.code, "version_conflict")

    def test_exact_source_quote_has_verified_offsets(self):
        quote = "用于校验原文片段与来源版本。"
        result = self.review(selections=[self.selection(quote=quote)])
        self.assertEqual(result.status, "ok")
        card = result.data.statutes[0]
        self.assertEqual(self.source()["text"][card.quote_start:card.quote_end], quote)
        self.assertEqual(card.source_ref.source_version, "v1")
        self.assertEqual(len(card.text_sha256), 64)

    def test_wrong_source_identity_version_and_invented_quote_are_rejected(self):
        for change in [{"source_id": "other"}, {"version": "latest"}, {"text": ""}, {"locator": {}},
                       {"locator": {"article": None}}, {"locator": {"page": 0}}, {"locator": "   "}]:
            with self.subTest(change=change):
                result = self.review(source={**self.source(), **change})
                self.assertEqual(result.status, "insufficient")
                self.assertFalse(result.data.statutes)
                self.assertFalse(result.source_refs)
                self.assertEqual(len(result.data.rejected), 1)
        self.assertEqual(len(self.review(selections=[self.selection(quote="不存在的引文")]).data.rejected), 1)

    def test_unsafe_url_and_invalid_metadata_are_rejected(self):
        for change in [{"url": "javascript:alert(1)"}, {"effective_from": "2026-02-30"}, {"court": ["bad"]}]:
            with self.subTest(change=change):
                self.assertEqual(len(self.review(source={**self.source(), **change}).data.rejected), 1)

    def test_cases_are_separate_and_missing_metadata_is_visible(self):
        source = self.source("case")
        source["court"] = None
        result = self.review(source, [self.selection("case")])
        self.assertEqual(result.status, "insufficient")
        self.assertEqual(len(result.data.cases), 1)
        self.assertFalse(result.data.statutes)
        self.assertTrue(result.data.cases[0].warnings)

    def test_source_kind_mismatch_is_rejected(self):
        result = self.review(self.source("case"))
        self.assertFalse(result.data.statutes)
        self.assertTrue(result.data.rejected)

    def test_repeated_quote_does_not_hide_location_ambiguity(self):
        result = self.review({**self.source(), "text": "重复片段。中间内容。重复片段。"}, [self.selection(quote="重复片段。")])
        self.assertEqual(result.status, "insufficient")
        self.assertEqual(result.data.statutes[0].quote_start, 0)
        self.assertTrue(any("出现多次" in item for item in result.data.statutes[0].warnings))

    def test_date_metadata_is_a_warning_not_an_applicability_judgment(self):
        result = self.review(reference_date="2019-12-31")
        self.assertEqual(result.status, "insufficient")
        self.assertTrue(any("早于" in value for value in result.data.statutes[0].warnings))
        self.assertTrue(any("未判断" in value for value in result.data.limitations))

    def test_multiple_versions_remain_separate(self):
        service = CaseResearchService(self.repo, source_resolver=lambda pointer: self.source(version=pointer.source_version))
        result = service.review_sources(self.request(selections=[self.selection(version="v1"), self.selection(version="v2")]))
        self.assertEqual(len(result.data.statutes), 2)
        self.assertTrue(all(any("多个选定版本" in value for value in card.warnings) for card in result.data.statutes))

    def test_repeated_selection_is_deduplicated(self):
        result = self.review(selections=[self.selection(), self.selection()])
        self.assertEqual(len(result.data.statutes), 1)
        self.assertEqual(len(result.source_refs), 1)

    def test_missing_resolver_and_empty_selection_are_explicit(self):
        service = CaseResearchService(self.repo)
        self.assertTrue(service.review_sources(self.request(selections=[self.selection()])).data.rejected)
        self.assertEqual(service.review_sources(self.request(selections=[])).status, "empty")

    def test_partial_source_failure_preserves_success(self):
        def resolver(pointer):
            if pointer.source_version == "v2":
                raise LookupError("synthetic unavailable source")
            return self.source()
        service = CaseResearchService(self.repo, source_resolver=resolver)
        result = service.review_sources(self.request(selections=[self.selection(), self.selection(version="v2")]))
        self.assertEqual(len(result.data.statutes), 1)
        self.assertEqual(len(result.data.rejected), 1)

    def test_expired_request_never_resolves_sources(self):
        def resolver(pointer):
            self.fail("must not run")
        service = CaseResearchService(self.repo, source_resolver=resolver)
        request = self.request(selections=[self.selection()])
        request["context"]["deadline_at"] = datetime.now(timezone.utc)-timedelta(seconds=1)
        self.assertEqual(service.review_sources(request).error.code, "timeout")

    def test_versionless_selection_is_invalid(self):
        selection = self.selection()
        selection["source_ref"]["source_version"] = None
        self.assertEqual(self.review(selections=[selection]).error.code, "invalid_request")

    def test_builtin_fee_source_works_through_composed_resolver(self):
        pointer = self.service.calculator.fee_rule.pointer
        result = self.service.review_research_sources(self.request(selections=[{"source_ref": pointer, "expected_kind": "statute"}], reference_date="2026-11-09"))
        self.assertEqual(result.status, "insufficient")
        self.assertTrue(any("短引文" in warning for warning in result.data.statutes[0].warnings))
        self.assertEqual(result.data.statutes[0].url, "https://www.court.gov.cn/zixun/xiangqing/5092.html")
        self.assertTrue(result.source_refs)


if __name__ == "__main__":
    unittest.main()
