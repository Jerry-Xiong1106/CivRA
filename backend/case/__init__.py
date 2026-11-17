"""Public case facade. Services and compatibility adapters are imported on demand."""

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
    'ToolInvocation': ('backend.tools.case_contracts', 'ToolInvocation'),
    'CaseToolClient': ('backend.tools.case_client', 'CaseToolClient'),
    'get_case_tool_contracts': ('backend.tools.case_tools', 'get_case_tool_contracts'),
    'get_contract_manifest': ('backend.tools.case_manifest', 'get_contract_manifest'),
    'to_runtime_tool_result': ('backend.tools.case_results', 'to_runtime_tool_result'),
    'runtime_case_view': ('backend.agent.case_adapter', 'runtime_case_view'),
    'to_evaluation_result': ('evaluation.adapters.case_results', 'to_evaluation_result'),
    'load_development_case': ('scripts.case.fixtures', 'load_development_case'),
    'CaseAssetStore': ('.storage.assets', 'SQLiteAssetStore'),
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
