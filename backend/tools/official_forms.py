"""Optional official-form tool adapter; business generation stays in case.drafting."""

from backend.case.drafting.official_forms import RecommendFormsRequest, PrepareFormRequest


class OfficialFormToolClient:
    """Opt-in Agent tool group, keeping the existing ten evaluation tools stable."""
    models = {"recommend_official_templates": RecommendFormsRequest, "prepare_official_form": PrepareFormRequest}

    def __init__(self, service):
        self.service = service

    @classmethod
    def contracts(cls):
        from backend.case.operations import BUSINESS_STAGES
        result = {}
        for name, model in cls.models.items():
            schema = model.model_json_schema()
            for key in ("schema_version", "context", "case_version"):
                schema["properties"].pop(key, None)
                if key in schema.get("required", []):
                    schema["required"].remove(key)
            result[name] = dict(name=name, owner="member3", parameters=schema,
                description="按案由推荐官方空白模板" if name.startswith("recommend") else "准备原表栏目、拟填写文字及待确认问题",
                stages=["litigation"] if name.startswith("prepare") else list(BUSINESS_STAGES),
                side_effects=name.startswith("prepare"), max_retries=0)
        return result

    def invoke(self, name, *, context, case_version, arguments):
        if name not in self.models or set(arguments) & {"context", "case_version", "schema_version"}:
            raise ValueError("unknown tool or protected argument")
        request = self.models[name](context=context, case_version=case_version, **arguments)
        method = self.service.recommend if name.startswith("recommend") else self.service.prepare
        return method(request)
