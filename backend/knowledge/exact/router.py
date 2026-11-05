"""Built-in exact route independent of keyword and vector algorithms."""
from .matcher import match_exact_query
from ..retrieval.results import no_results, to_result


class ExactRouter:
    def __init__(self, provisions):
        self.lookup = {(p["law_title"], p["article_no"]): p for p in provisions}
        if len(self.lookup) != len(provisions):
            raise ValueError("Exact routing requires unambiguous law/article keys")

    def search(self, query: str) -> dict | None:
        """None means no explicit target; a recognized miss stops retrieval."""
        target = match_exact_query(query)
        if target is None:
            return None
        provision = self.lookup.get(target)
        if provision is not None:
            result = to_result(provision, 1, 1.0, ["exact"])
            result["retrieval"].update({"score_type": "exact", "channels": {"exact": {"rank": 1, "score": 1.0}}})
            return {"status": "ok", "query": query, "results": [result], "warnings": []}
        if any(char.isdigit() for char in target[1]):
            return no_results(query, "unsupported_article_number",
                              "当前精确查询尚不支持阿拉伯数字条号，请改用中文条号；这不表示库中没有该法条。")
        return no_results(query, "exact_article_not_found",
                          f"当前语料未找到《{target[0]}》{target[1]}，未返回相似条文替代；这不表示该法条在库外不存在。")
