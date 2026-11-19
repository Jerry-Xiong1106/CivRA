"""Public case facade: business models and services only, imported on demand.

Tool, Agent and evaluation adapters live in their own packages; import them from there.
"""

from importlib import import_module

_EXPORTS = {
    'BusinessOutput': ('.models', 'BusinessOutput'),
    'BusinessResult': ('.models', 'BusinessResult'),
    'CaseSnapshot': ('.models', 'CaseSnapshot'),
    'Evidence': ('.models', 'Evidence'),
    'Fact': ('.models', 'Fact'),
    'Money': ('.models', 'Money'),
    'CaseService': ('.service', 'CaseService'),
    'check_evidence': ('.materials.evidence', 'check_evidence'),
    'build_evidence_directory': ('.materials.evidence', 'build_evidence_directory'),
    'update_case': ('.intake.updates', 'update_case'),
    'validate_case_snapshot': ('.intake.validation', 'validate_case_snapshot'),
    'SQLiteCaseRepository': ('.storage.repository', 'SQLiteCaseRepository'),
    'SQLiteAssetStore': ('.storage.assets', 'SQLiteAssetStore'),
    'OcrEngineRegistry': ('.materials.ocr', 'OcrEngineRegistry'),
    'PaddleOcrConfig': ('.materials.ocr', 'PaddleOcrConfig'),
    'PaddleOcrEngine': ('.materials.ocr', 'PaddleOcrEngine'),
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module, attribute = _EXPORTS[name]
    value = getattr(import_module(module, __name__), attribute)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
