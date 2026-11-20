"""Optional official-form tool adapter; business generation stays in case.drafting."""

from backend.case.drafting.official_forms import RecommendFormsRequest, PrepareFormRequest
from backend.common.schemas import ToolResult
from backend.tools.policy import set_policy
from langchain.tools import tool


def create_form_lookup(service):
    @tool("find_form_template", description=(
        "从本地官方空白文书目录按案由关键词和文书类型查表。被告答辩用 defense，原告起诉用 complaint。"
        "query 填材料上的案由或标题关键词，可留空分页浏览。候选含空白 Word 链接，以及可用于 "
        "draft_document 的模板参数（如有）。多项匹配时说明区别；案由未知时读取材料或追问。"
        "提供空白表可直接给 source_url，勿将空白表称为已填写的文书。"),
        args_schema={"type": "object", "additionalProperties": False, "properties": {
            "query": {"type": "string", "maxLength": 160},
            "document_type": {"enum": ["complaint", "defense", "other"]},
            "domain": {"enum": ["民事", "刑事", "行政", "国家赔偿", "其他程序"]},
            "offset": {"type": "integer", "minimum": 0},
        }})
    def find_form_template(query="", document_type=None, domain=None, offset=0):
        try:
            rows = service.official_forms.library.find_blanks(query, document_type, domain)
        except (OSError, ValueError):
            return ToolResult.fail("UPSTREAM", "本地原表目录不可读取。").model_dump(mode="json")
        drafts = {t.template_id: t.template_version for t in service.drafter.templates.list_templates()}
        cards = []
        for row in rows[offset:offset + 8]:
            form, version = row["template_id"], row["version"]
            draft = "cn_complaint_" + (row["dispute_type"] or "")
            params = None
            if row["filling_supported"] and row["document_type"] == "complaint" and draft in drafts:
                params = {"template_id": draft, "template_version": drafts[draft]}
            cards.append({k: row[k] for k in ("title", "domain", "document_type", "template_id", "version",
                                               "filling_supported", "source_url")} | {
                "blank_download_path": f"/api/form-templates/{form}/{version}",
                "draft_document_args": params,
            })
        end = offset + len(cards)
        return ToolResult(status="ok" if cards else "empty",
            summary=f"共匹配 {len(rows)} 份空白原表，本页 {len(cards)} 份。",
            data={"candidates": cards, "total": len(rows),
                  "next_offset": end if end < len(rows) else None}).model_dump(mode="json")

    return set_policy(find_form_template, owner="member3", side_effects=False, max_retries=0)


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
