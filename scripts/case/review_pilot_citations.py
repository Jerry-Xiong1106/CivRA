"""Audit published pilot source bindings offline without rewriting answers or scores."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.case import CaseService, SQLiteCaseRepository, load_development_case
from backend.case.research.knowledge_bridge import CorpusSourceResolver
from backend.case.models import CaseSnapshot, SourcePointer


def review(results):
    resolver = CorpusSourceResolver.from_directory()
    rows = []
    with TemporaryDirectory() as temp:
        repository = SQLiteCaseRepository(Path(temp) / 'cases.db')
        service = CaseService(repository, source_resolver=resolver)
        for path in sorted(Path(results).glob('pilot-*-rag.json')):
            raw_bytes = path.read_bytes()
            record = json.loads(raw_bytes)
            answer = record['output']
            matches = record['retrieval']['matches']
            refs = []
            for hit in matches:
                pointer = SourcePointer(source_id=hit['source_id'], source_version=hit['source_version'])
                if resolver(pointer)['text'] != hit['text']:
                    raise ValueError('published candidate differs from pinned corpus: ' + hit['source_id'])
                refs.append(pointer)
            raw = load_development_case().model_dump(mode='json')
            raw.update(case_id='citation_review_' + record['task_id'], facts=[], evidence=[], claims=[],
                       dispute_type=None, user_goals=['仅检查已公开回答的引用结构'], stage='consultation', selected_route=None)
            case = repository.create_case(CaseSnapshot.model_validate(raw))
            # Only explicit full ID markers can form a binding. Never infer a missing ID from an article number.
            citations = []
            for pointer in refs:
                marker = '[' + pointer.source_id + ']'
                for match in re.finditer(re.escape(marker), answer):
                    start = answer.rfind('\n', 0, match.start()) + 1
                    end = answer.find('\n', match.end())
                    citations.append(dict(start=start, end=len(answer) if end < 0 else end, source_ref=pointer))
            report = service.review_answer(dict(
                context=dict(case_id=case.case_id, request_id='review-' + record['task_id'],
                             experiment_run_id=record['experiment_run_id'], deadline_at=None),
                case_version=case.case_version, answer=answer, retrieved_sources=refs, citations=citations))
            if report.status == 'error':
                raise RuntimeError(report.error)
            rows.append(dict(task_id=record['task_id'], original_sha256=hashlib.sha256(raw_bytes).hexdigest(),
                             original_output_unchanged=answer == record['output'],
                             citation_audit=report.data.citation_audit,
                             findings=[f.model_dump(mode='json') for f in report.data.findings],
                             checks_not_performed=report.data.checks_not_performed))
    if not rows:
        raise ValueError('no RAG pilot outputs found')
    return dict(scope='offline structural audit; not legal scoring or a new generation',
                corpus=resolver.manifest(), tasks=rows,
                missing_valid_bindings=sum(r['citation_audit']['valid_citation_count'] == 0 for r in rows))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, default=ROOT / 'evaluation/results/20261109T104304Z-dd61ec')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = review(args.results)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'reviewed':len(report['tasks']), 'missing_valid_bindings':report['missing_valid_bindings'],
                      'semantic_review_executed':False}, ensure_ascii=False))
