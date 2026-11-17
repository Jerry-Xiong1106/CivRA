"""Public synthetic case fixture for development scripts and tests."""

import hashlib
from pathlib import Path

from backend.case.models import CaseSnapshot

DEVELOPMENT_CASE = Path(__file__).resolve().parents[2] / "data/examples/member3/traffic_accident_v1/case_snapshot.json"


def load_development_case() -> CaseSnapshot:
    """Load public synthetic fixture, checking material hashes and span/quote alignment."""
    snapshot = CaseSnapshot.model_validate_json(DEVELOPMENT_CASE.read_text(encoding="utf-8"))
    if not snapshot.synthetic:
        raise ValueError("the development fixture must be explicitly synthetic")
    directory = DEVELOPMENT_CASE.parent / "materials"
    for evidence in snapshot.evidence:
        path = (directory / evidence.file_name).resolve()
        if not path.is_relative_to(directory.resolve()):
            raise ValueError("fixture material path escapes its directory")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != evidence.content_sha256:
            raise ValueError(f"fixture checksum differs: {evidence.file_id}")
        text = content.decode("utf-8")
        refs = list(evidence.material_refs)
        refs += [ref for fact in snapshot.facts for ref in fact.material_refs if ref.file_id == evidence.file_id]
        for ref in refs:
            if ref.span_start is not None:
                if ref.span_end > len(text) or (ref.quote is not None and text[ref.span_start:ref.span_end] != ref.quote):
                    raise ValueError(f"fixture span/quote mismatch: {evidence.file_id}")
    return snapshot
