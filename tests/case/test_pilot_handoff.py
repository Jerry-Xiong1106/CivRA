"""Independent integration checks for corpus, citations and principal accounting."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backend.case import CaseService, SQLiteCaseRepository, load_development_case
from backend.case.research.knowledge_bridge import CorpusSourceResolver, executable_research_plan
from backend.case.models import CaseSnapshot, SourcePointer


class PilotFixture(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = SQLiteCaseRepository(Path(self.temp.name) / 'cases.db')
        self.service = CaseService(self.repo)

    def case(self, *, principal='10000', has_repaid=True, repaid='2000', domain='private_lending', extra=None):
        raw = load_development_case().model_dump(mode='json')
        prototype = deepcopy(next(f for f in raw['facts'] if f['fact_id'] == 'f_claimant'))
        raw.update(dispute_type=domain, facts=[], evidence=[], claims=[], stage='consultation', selected_route=None)
        for name, kind, value in [('loan_principal', 'money', principal), ('has_repaid', 'boolean', has_repaid),
                                   ('repaid_principal', 'money', repaid)]:
            if value is None:
                continue
            f = deepcopy(prototype)
            f.update(fact_id=name, field=name, value_type=kind,
                     value={'amount': value, 'currency':'CNY'} if kind == 'money' else value,
                     evidence_ids=[], material_refs=[])
            raw['facts'].append(f)
        if extra:
            extra(raw)
        case = self.repo.create_case(CaseSnapshot.model_validate(raw))
        self.case_id = case.case_id
        return case

    def request(self, **extra):
        return {'context': {'case_id': self.case_id, 'request_id':'pilot', 'experiment_run_id':'synthetic', 'deadline_at':None},
                'case_version':self.repo.get_case(self.case_id).case_version, **extra}

    def calculate(self, **extra):
        inputs = {'principal_fact_id':'loan_principal', 'has_repaid_fact_id':'has_repaid'}
        if any(f.fact_id == 'repaid_principal' for f in self.repo.get_case(self.case_id).facts):
            inputs['repaid_principal_fact_id'] = 'repaid_principal'
        return self.service.calculate_costs(self.request(calculation_type='principal_balance',
            rule_version='confirmed_principal_v1', inputs={**inputs, **extra}))


class PrincipalTests(PilotFixture):
    def test_partial_repayment_uses_confirmed_principal(self):
        self.case(domain='lending')
        result = self.calculate()
        self.assertEqual(result.status, 'ok', result.error)
        self.assertEqual(result.data.amounts['remaining_principal'].amount, '8000')
        self.assertEqual(result.data.rule_refs, [])

    def test_unknown_repayment_is_not_zero(self):
        self.case(has_repaid=None, repaid=None)
        result = self.calculate()
        self.assertEqual(result.status, 'insufficient')
        self.assertEqual(result.data.amounts, {})
        report = self.service.check_evidence(self.request(case_snapshot=self.repo.get_case(self.case_id)))
        self.assertTrue(any(q.field == 'has_repaid' for q in report.data.suggested_questions))

    def test_explicit_no_repayment_and_full_repayment(self):
        self.case(has_repaid=False, repaid=None)
        result = self.calculate()
        self.assertEqual(result.data.amounts['remaining_principal'].amount, '10000')
        report = self.service.check_evidence(self.request(case_snapshot=self.repo.get_case(self.case_id)))
        self.assertFalse(any(q.field == 'repaid_principal' for q in report.data.suggested_questions))

    def test_repayment_requires_principal_allocation(self):
        self.case(repaid=None)
        self.assertEqual(self.calculate().status, 'insufficient')
        report = self.service.check_evidence(self.request(case_snapshot=self.repo.get_case(self.case_id)))
        self.assertTrue(any(q.field == 'repaid_principal' for q in report.data.suggested_questions))

    def test_overpayment_is_not_clamped_to_zero(self):
        self.case(repaid='10001')
        self.assertEqual(self.calculate().data.amounts, {})

    def test_complete_repayment_outputs_exact_zero(self):
        self.case(repaid='10000')
        self.assertEqual(self.calculate().data.amounts['remaining_principal'].amount, '0')

    def test_conflicting_repayment_state_is_not_ignored(self):
        self.case(has_repaid=False)
        self.assertEqual(self.calculate().status, 'insufficient')

    def test_unconfirmed_amount_withholds_balance(self):
        def change(raw):
            raw['facts'][0].update(confirmation_status='unconfirmed', confirmation=None)
        self.case(extra=change)
        self.assertEqual(self.calculate().data.amounts, {})

    def test_conflicting_amounts_withhold_balance(self):
        def change(raw):
            alternate=deepcopy(raw['facts'][0]); alternate['fact_id']='other-principal';alternate['value']['amount']='9000'
            alternate.update(confirmation_status='unconfirmed',confirmation=None)
            raw['facts'].append(alternate)
        self.case(extra=change)
        self.assertEqual(self.calculate().data.amounts, {})

    def test_currency_mismatch(self):
        self.case(extra=lambda raw: raw['facts'][2]['value'].update(currency='USD'))
        self.assertEqual(self.calculate().data.amounts, {})

    def test_high_precision_is_preserved(self):
        self.case(principal='1000000000000000000000000000000.03', repaid='0.02')
        self.assertEqual(self.calculate().data.amounts['remaining_principal'].amount, '1000000000000000000000000000000.01')

    def test_stale_version_and_untrusted_amount_argument(self):
        self.case()
        self.assertEqual(self.calculate(amount='8000').status, 'error')
        request=self.request(calculation_type='principal_balance', rule_version='confirmed_principal_v1', inputs={})
        request['case_version']=2
        self.assertEqual(self.service.calculate_costs(request).error.code, 'version_conflict')


class CorpusBridgeTests(PilotFixture):
    def setUp(self):
        super().setUp()
        self.resolver=CorpusSourceResolver.from_directory()
        self.pointer=SourcePointer(source_id='中华人民共和国民法典::2020-05-28::第六百八十条', source_version='2021-01-01')

    def test_pinned_corpus_resolves_complete_text_and_pages(self):
        record=self.resolver(self.pointer)
        self.assertEqual(self.resolver.manifest()['source_count'],1690)
        self.assertIn('约定不明确',record['text'])
        self.assertEqual(record['page_start'],97)
        record['text']='modified'
        self.assertNotEqual(self.resolver(self.pointer)['text'],'modified')

    def test_wrong_version_never_falls_back(self):
        with self.assertRaises(KeyError):
            self.resolver(self.pointer.model_copy(update={'source_version':'2022-01-01'}))

    def test_source_card_keeps_pdf_metadata_and_homepage_warning(self):
        self.case()
        service=CaseService(self.repo,source_resolver=self.resolver)
        result=service.review_research_sources(self.request(selections=[{'source_ref':self.pointer,'expected_kind':'statute'}]))
        self.assertEqual(len(result.data.statutes),1)
        card=result.data.statutes[0]
        self.assertEqual(card.metadata['page_start'],97)
        self.assertTrue(any('首页' in w for w in card.warnings))

    def test_executable_plan_reports_unsupported_capabilities(self):
        self.case()
        plan=self.service.prepare_research(self.request()).data
        converted=executable_research_plan(plan)
        self.assertEqual(len(converted['requests']),2)
        self.assertEqual(len(converted['deferred']),1)
        self.assertEqual(set(converted['requests'][0]['arguments']),{'query','top_k'})
        self.assertIn('incident_date',converted['requests'][0]['unapplied_filters'])
        self.assertFalse(executable_research_plan(plan,expected_snapshot='other')['requests'])


class CitationReviewTests(PilotFixture):
    def setUp(self):
        super().setUp()
        self.resolver=CorpusSourceResolver.from_directory()
        self.pointer=SourcePointer(source_id='中华人民共和国民法典::2020-05-28::第六百八十条', source_version='2021-01-01')
        self.case()
        self.service=CaseService(self.repo,source_resolver=self.resolver)
        self.answer='这是需要核对的法律结论。'

    def review(self, **extra):
        payload={'answer':self.answer,'retrieved_sources':[self.pointer],
                 'citations':[{'start':0,'end':len(self.answer),'source_ref':self.pointer}],
                 'required_citation_spans':[{'start':0,'end':len(self.answer)}]}
        payload.update(extra)
        return self.service.review_answer(self.request(**payload))

    def test_valid_binding_is_not_semantic_approval(self):
        result=self.review()
        self.assertEqual(result.data.citation_audit['valid_citation_count'],1)
        self.assertEqual(result.data.citation_audit['covered_span_count'],1)
        self.assertIn('semantic_citation_support',result.data.checks_not_performed)
        self.assertEqual(result.data.disposition,'needs_review')

    def test_missing_citations_detected_even_with_candidates(self):
        result=self.review(citations=[])
        self.assertEqual(result.data.citation_audit['covered_span_count'],0)
        self.assertTrue(any('没有有效' in f.message for f in result.data.findings))

    def test_unknown_inline_source_is_detected(self):
        result=self.review(answer=self.answer+'[invented::2026::第一条]')
        self.assertTrue(any('未返回' in f.message for f in result.data.findings))

    def test_source_with_wrong_version_is_rejected(self):
        result=self.review(citations=[{'start':0,'end':len(self.answer),
            'source_ref':self.pointer.model_copy(update={'source_version':'bad'})}])
        self.assertEqual(result.data.citation_audit['valid_citation_count'],0)

    def test_quote_is_checked_against_exact_original(self):
        result=self.review(citations=[{'start':0,'end':len(self.answer),'source_ref':self.pointer,'source_quote':'原文中不存在的句子'}])
        self.assertTrue(any('连续片段' in f.message for f in result.data.findings))

    def test_out_of_range_and_duplicate_bindings(self):
        citation={'start':0,'end':len(self.answer)+100,'source_ref':self.pointer}
        result=self.review(citations=[citation,citation])
        self.assertEqual(result.data.citation_audit['valid_citation_count'],0)
        self.assertTrue(any('重复登记' in f.message for f in result.data.findings))

    def test_partial_overlap_does_not_count_as_full_coverage(self):
        result=self.review(citations=[{'start':1,'end':len(self.answer),'source_ref':self.pointer}])
        self.assertEqual(result.data.citation_audit['covered_span_count'],0)

    def test_arithmetic_only_answer_can_explicitly_omit_legal_sources(self):
        result=self.review(citations=[],retrieved_sources=[],required_citation_spans=[],requires_citations=False)
        self.assertFalse(any(f.category=='citation' for f in result.data.findings))

    def test_review_does_not_change_case(self):
        before=self.repo.get_case(self.case_id).model_dump()
        self.review()
        self.assertEqual(before,self.repo.get_case(self.case_id).model_dump())


if __name__=='__main__':
    unittest.main()
