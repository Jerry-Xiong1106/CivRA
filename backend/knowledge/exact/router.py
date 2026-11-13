"""Built-in exact route independent of keyword and vector algorithms."""
from .matcher import UnsupportedExactQuery, match_exact_query
from ..retrieval.results import no_results, to_result


class ExactRouter:
    def __init__(self, provisions):
        self.lookup = {(p["law_title"], p["article_no"]): p for p in provisions}
        if len(self.lookup) != len(provisions):
            raise ValueError("Exact routing requires unambiguous law/article keys")

    def search(self, query: str) -> dict | None:
        """None means no explicit target; a recognized miss stops retrieval."""
        try:
            targets = match_exact_query(query)
        except UnsupportedExactQuery as exc:
            return no_results(query, "unsupported_exact_query", str(exc))
        if targets is None:
            return None
        missing = [f"《{law}》{article}" for law, article in targets if (law, article) not in self.lookup]
        if missing:
            return no_results(query, "exact_article_not_found",
                              f"当前语料未找到：{'、'.join(missing)}。本次未返回部分命中或相似条文替代；这不表示该法条在库外不存在。")
        results = []
        for rank, target in enumerate(targets, 1):
            result = to_result(self.lookup[target], rank, 1.0, ["exact"])
            result["retrieval"].update({"score_type": "exact", "channels": {"exact": {"rank": rank, "score": 1.0}}})
            results.append(result)
        return {"status": "ok", "query": query, "results": results, "warnings": []}
