# 功能4：检索请求准备与来源整理

更新日期：2026-11-09。业务模块版本：`case-research-0.1.0`。

本次完成成员三能够独立交付的部分：准备查询、整理有版本的来源、核对引文位置。当前本地副本没有成员二的真实检索实现，也未检查远程新提交；不能由此推断成员二尚未开发。

## 功能与边界

- 从共享仓库读取指定案件版本，生成法条/案例查询建议，保留案件、事实、诉求和知识库版本。
- 纠纷词、诉求词来自明确的结构化类型；未知类型使用通用查询并记录待明确项。
- 只有已确认且没有冲突的事件日期才进入日期过滤。事件日期与检索截止日期分开；日期冲突不自动选值。
- 查询文字不包含姓名、联系方式、完整用户陈述或未确认事实。不调用网络，不自动选择模型，不替成员四决定何时追加检索。
- 通过注入的精确版本读取器整理法条与案例卡片；ID/版本不匹配、定位缺失、原文为空、引文不存在、类型不符或不合法URL都会被拒绝。
- 引文保留返回文本中的起止位置（Python Unicode码点索引，右侧不含）及文本SHA-256；重复出现的引文明确提示定位歧义。同一来源的不同版本不自动合并。
- 法条和案例分别输出；生效/结束日期、法院、裁判日期等仅使用提供的元数据。日期提示不等于法律适用判断，类案相似性和裁判差异仍待真实检索及模型分析。
- 某个来源失败时保留其他成功结果；读取器仅提供短引文时，明确限制定位和核对范围。

## Python入口

通过 `CaseService.prepare_research`、`CaseService.review_research_sources` 调用，或单独实例化 `CaseResearchService`。两项都是新增的独立Python入口，未改动原十个Agent工具的请求/返回模型或注册表。

```python
from backend.case import CaseService, SQLiteCaseRepository

repo = SQLiteCaseRepository("var/cases.sqlite3")
service = CaseService(repo, source_resolver=member2_exact_source_resolver)
snapshot = repo.get_case(case_id)
context = {
    "request_id": "research-01", "case_id": case_id,
    "experiment_run_id": None, "deadline_at": None,
}
prepared = service.prepare_research({
    "context": context,
    "case_version": snapshot.case_version,
    "knowledge_version": actual_knowledge_version,
    "as_of_date": "2026-11-09",
    "topics": ["liability", "losses", "similar_cases"],
    "top_k": 5,
})
```

每条 `queries` 包含 `query_id`、`tool`（`search_statutes`/`search_cases`）、`query`、`filters`、`knowledge_version`、`top_k`、`fact_ids`、`claim_ids`。`procedure`可作为额外topic明确选择。`top_k`限1—20。

`filters`沿用成员五草案：`domain`、`jurisdiction`、`incident_date`、`as_of_date`。具体取值、空值和时间边界语义仍需成员二确认；不支持的过滤字段不能静默忽略。知识库版本未提供时返回`insufficient`及待明确项，不能把占位版本作为真实知识库使用。

真实检索由成员二接口执行，成员四决定使用哪些查询。获得明确来源ID与版本后：

```python
reviewed = service.review_research_sources({
    "context": {**context, "request_id": "review-sources-01"},
    "case_version": snapshot.case_version,
    "reference_date": "2026-05-12",
    "selections": [{
        "source_ref": {"source_id": source_id, "source_version": source_version},
        "expected_kind": "statute",  # 或 case
        "quote": exact_quote,        # 可省略；省略时展示返回文本开头最多400字
    }],
})
```

单次最多30个来源选择。结果为 `statutes`、`cases`、`rejected` 和 `limitations`。卡片包含来源、标题、定位、链接、引文区间、文本哈希、元数据及提示。`reference_date`用于法条效力元数据提示，不自动决定司法解释过渡规则，也不用于推定类案是否相似。

`empty`表示明确没有来源选择；`insufficient`表示部分来源拒绝或有待核对信息。`ok`仅表示当前定位检查通过，不表示已完成法律适用、引文支持关系或专业评审。

## 成员二来源读取器的适配

沿用已有业务来源读取约定：`resolver(SourcePointer) -> Mapping`，必须返回同ID、同版本的以下字段：

| 字段 | 约定 |
|---|---|
| `source_id`、`version` | 必须与请求完全一致，不自动取最新版本 |
| `source_type` | `statute`/`law`/`regulation`/`judicial_interpretation`归入法条；`case`归入案例 |
| `title` | 非空标题 |
| `locator` | 非空字符串，或至少提供有效article/page/paragraph/chunk_id的字典 |
| `text` | 可读取的同版本原文或明确许可的摘录；单项上限500000字符 |
| `url` | HTTP(S)链接或空值 |
| 日期/案例元数据 | 可选`effective_from`、`effective_to`、`verified_on`、`decision_date`、`court`、`jurisdiction`、`dispute_type` |
| `text_scope` | 若为`short_excerpt_only`，明确提示不能据此核验完整条款 |

如果成员二最终接口使用`source_ref.source_version`等嵌套字段，在适配函数中映射到此结构；本模块没有另建共享`SourceRef`或替代知识库。每张卡片的区间只针对读取器返回的`text`，不冒充整份PDF的坐标。

`CaseService.get_source`组合外部读取器和第五部分的本地费用规则来源。`member3.local_rule.*`仅用于本地已核对规则，独立命名、明确版本；不冒用成员二知识库ID。本地费用来源只缓存短引文与官方定位，完整条文仍需官方页面或成员二提供。

## 对接与验收

| 成员 | 需要对接 | 验收重点 |
|---|---|---|
| 成员2 | 检索参数映射、知识库manifest/版本、精确版本原文、日期语义和案例元数据 | 返回来源可定位，空结果明确，不偷换版本；实际检索效果独立评估 |
| 成员4 | 使用/改写查询、追加检索、法律与类案差异分析，决定是否注册新Python入口 | 不把候选查询当结论，不跳过未知事实或引用限制 |
| 成员1 | 来源卡片、原文跳转、版本与失败提示展示 | 用户看到原文、定位和未核验范围 |
| 成员5 | 独立检索相关性、引用支持关系及类案差异标注 | 合成来源仅测接口，不当作真实判例或检索准确率证据 |

测试：`tests/case/test_research.py`共19项；联合演示：`scripts/case/demo_research_costs.py`。本次未实现真实检索、RAG索引、自动法律适用或类案胜诉率。
