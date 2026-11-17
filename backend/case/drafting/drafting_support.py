"""Validate gateway references and exact source versions before drafting."""
from collections import Counter
from copy import deepcopy

from ..business_support import deadline, unique
from ..research.sources import CaseResearchService, SourceSelection, review_sources


def validate_reasoning(paragraphs, facts, sources):
    if not isinstance(paragraphs,list) or len(paragraphs)>20:
        raise ValueError("reasoning must contain at most 20 paragraphs")
    allowed={"fact_ids":{f.fact_id for f in facts},"source_ids":{s["source_id"] for s in sources}}
    for paragraph in paragraphs:
        if not isinstance(paragraph,dict) or set(paragraph)!={"text","fact_ids","source_ids"}:
            raise ValueError("invalid reasoning paragraph")
        if not isinstance(paragraph["text"],str) or not paragraph["text"].strip() or len(paragraph["text"])>6000:
            raise ValueError("invalid reasoning text")
        for key,ids in allowed.items():
            refs=paragraph[key]
            if not isinstance(refs,list) or not 0<len(refs)<=50 or any(not isinstance(x,str) for x in refs):
                raise ValueError("invalid reasoning references")
            if len(refs)!=len(set(refs)) or set(refs)-ids:
                raise ValueError("reasoning used unknown or duplicate references")
    return deepcopy(paragraphs)


def checked_sources(request, pointers, repository, resolver):
    if not pointers:
        return [],[],[]
    if resolver is None:
        return [],[],["未接入精确版本法律原文读取器。"]
    cache, selections, issues = {}, [], []
    for pointer in unique(pointers):
        deadline(request)
        try:
            if pointer.source_version is None:
                raise ValueError("需要明确来源版本")
            key=(pointer.source_id,pointer.source_version)
            if key not in cache:
                cache[key]=deepcopy(dict(resolver(pointer)))
            kind="case" if cache[key].get("source_type")=="case" else "statute"
            selections.append(SourceSelection(source_ref=pointer,expected_kind=kind))
        except TimeoutError:
            raise
        except Exception as exc:
            issues.append(f"{pointer.source_id}：未采用来源（{type(exc).__name__}）。")
    reader=CaseResearchService(repository,source_resolver=lambda p:cache[(p.source_id,p.source_version)])
    reviewed, rejected=review_sources(request,selections,reader.review_sources)
    cards=list(reviewed.values())
    counts=Counter(card.source_ref.source_id for card in cards)
    legal=[]
    for card in cards:
        issues.extend(card.warnings)
        raw=cache[(card.source_ref.source_id,card.source_ref.source_version)]
        if counts[card.source_ref.source_id]>1:
            issues.append(f"{card.source_ref.source_id}：多个版本尚待明确，本次不用于生成理由。")
        elif raw.get("text_scope")=="short_excerpt_only":
            issues.append(f"{card.source_ref.source_id}：仅有短摘录，本次不用于生成实体理由。")
        elif card.source_type=="statute":
            legal.append(raw)
    return legal,cards,unique(issues+rejected)
