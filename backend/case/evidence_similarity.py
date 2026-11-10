"""Bounded, source-verified text similarity hints; never delete or merge evidence."""

from dataclasses import dataclass
import hashlib
from itertools import combinations
import re
import unicodedata

from .models import Finding, SuggestedQuestion


SIMILARITY_VERSION = "material-text-similarity-0.1.0"


@dataclass(frozen=True)
class SimilarityPolicy:
    min_characters: int = 80
    max_characters: int = 20000
    min_unique_trigrams: int = 20
    threshold: float = 0.92
    max_documents: int = 100
    max_pairs: int = 2000
    max_findings: int = 100

    def __post_init__(self):
        if not (3 <= self.min_characters <= self.max_characters and 0 < self.threshold <= 1
                and min(self.min_unique_trigrams, self.max_documents, self.max_pairs, self.max_findings) > 0):
            raise ValueError("invalid similarity limits")


def read_material_text(assets, case_id, evidence):
    """Resolve registered extraction pages, validating the original quoted spans first."""
    record, _ = assets.get_file(case_id, evidence.file_id, evidence.file_version)
    if record.content_sha256 != evidence.content_sha256:
        raise ValueError("evidence hash differs from the registered file")
    pages = {}
    versions = {}
    for ref in evidence.material_refs:
        if not ref.extraction_version:
            raise ValueError("material has no saved extraction version")
        assets.read_extraction(case_id, ref)
        page_key = (ref.page, ref.paragraph if ref.page is None else None)
        if page_key in versions and versions[page_key] != ref.extraction_version:
            raise ValueError("one page refers to multiple extraction versions")
        versions[page_key] = ref.extraction_version
        if page_key not in pages:
            full_ref = ref.model_copy(update={"span_start": None, "span_end": None, "quote": None,
                "polygon": None, "paragraph": ref.paragraph or ("text" if ref.page is None else None)})
            pages[page_key] = assets.read_extraction(case_id, full_ref)
    return "\n".join(pages[key] for key in sorted(pages, key=lambda key: (key[0] or 0, key[1] or "")))


def inspect_text_similarity(evidence, text_reader=None, *, policy=None, deadline_check=None):
    """Return findings/questions/check names/limitations without changing any snapshot.

    Only same-type, different-hash materials with enough saved text are compared.
    Different numeric tokens suppress a near-match; equal amounts alone never imply duplication.
    """
    policy = policy or SimilarityPolicy()
    if text_reader is None:
        return [], [], [], ["本次未接入已保存提取文本读取器，未执行文字近似重复检测。"]
    documents = sorted((item for item in evidence if item.status != "excluded"), key=lambda item: item.evidence_id)
    prepared, limitations, findings, questions = [], [], [], []
    if len(documents) > policy.max_documents:
        limitations.append(f"文字近似重复检测仅覆盖前{policy.max_documents}份材料，其余未比对。")
    for item in documents[:policy.max_documents]:
        if deadline_check:
            deadline_check()
        try:
            text = text_reader(item)
            if not isinstance(text, str):
                raise ValueError("text reader must return a string")
        except Exception as exc:
            limitations.append(f"材料{item.evidence_id}未进行文字近似比对：无法核实已保存提取文本（{type(exc).__name__}）。")
            continue
        # Limit raw input before normalization; do not silently compare truncated documents.
        if len(text) > policy.max_characters:
            limitations.append(f"材料{item.evidence_id}超过文字比对长度上限，未进行近似比对。")
            continue
        normalized = "".join(unicodedata.normalize("NFKC", text).casefold().split())
        grams = {normalized[index:index+3] for index in range(max(0, len(normalized)-2))}
        if len(normalized) < policy.min_characters or len(grams) < policy.min_unique_trigrams:
            limitations.append(f"材料{item.evidence_id}可用文字过少，未进行近似比对。")
            continue
        numbers = re.findall(r"[0-9]+(?:\.[0-9]+)?", normalized)
        prepared.append((item, normalized, grams, numbers))
    checked = 0
    for left, right in combinations(prepared, 2):
        if deadline_check:
            deadline_check()
        first, first_text, first_grams, first_numbers = left
        second, second_text, second_grams, second_numbers = right
        if first.content_sha256 == second.content_sha256 or first.document_type != second.document_type:
            continue
        if checked >= policy.max_pairs or len(findings) >= policy.max_findings:
            limitations.append("文字近似重复检测达到比对/结果数量上限，剩余材料组合未检查。")
            break
        checked += 1
        if first_numbers != second_numbers:
            continue
        score = 2 * len(first_grams & second_grams) / (len(first_grams) + len(second_grams))
        if score < policy.threshold:
            continue
        ids = [first.evidence_id, second.evidence_id]
        digest = hashlib.sha256(":".join([SIMILARITY_VERSION, *ids]).encode()).hexdigest()[:16]
        basis = "规范化后的已提取文字一致" if first_text == second_text else f"已提取文字三字片段相似度为{score:.3f}"
        message = f"{basis}，疑似同一材料的不同上传版本；相似度不是重复概率，仍需核对原图、票据编号和金额。"
        finding = Finding(finding_id="similar_"+digest, category="duplicate", severity="warning",
            message=message, evidence_ids=ids,
            material_refs=[*first.material_refs[:3], *second.material_refs[:3]],
            suggested_correction="请用户确认是否同一材料；确认前不排除文件、不合并事实、不扣减金额，原件和来源继续保留。")
        findings.append(finding)
        questions.append(SuggestedQuestion(question_id="q_similar_"+digest, field="evidence.possible_duplicate",
            text="这两份文字相似的材料是否为同一张票据或同一份文件？请核对原图并明确保留哪一份。",
            reason=message, evidence_ids=ids))
    checks = ["saved_material_text_similarity_v1"] if checked else []
    if not checked:
        limitations.append("没有符合条件的不同文件材料组合，文字近似比对未实际执行。")
    limitations.append("文字近似检测仅覆盖可回查的已提取文字及同类型材料；短文本、数字识别差异、裁剪/旋转或无可用OCR文字可能漏检。")
    return findings, questions, checks, limitations
