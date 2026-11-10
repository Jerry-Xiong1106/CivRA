# RAG 与 Agent 接入讨论稿

状态：交接讨论稿。以下区分当前实现与接入建议，工具接口和公共类型待双方确认后落地。

## 1. 当前实现

RAG 模块位于 `backend/knowledge/`，统一 Python 入口为 `RetrievalService`。

- 语料：民法典、民事诉讼法、道路交通安全法，共 1690 条法条。
- 默认检索：精确条号路由＋metadata 向量检索，`top_k=5`。
- 向量方案：SiliconFlow `BAAI/bge-m3`，1024 维，本地精确余弦检索。
- 可选通道：BM25、向量检索、BM25＋向量 RRF 融合。
- 返回内容：法条原文、条号、来源、版本元数据和检索诊断信息。

当前已有检索核心、CLI 和评测入口，尚未实现 Agent 工具适配与注册。案例检索、日期/效力过滤、多版本检索和 reranker 暂不在本次接入范围内。

## 2. 调用方式与工具参数

当前可以直接调用：

```python
from backend.knowledge.retrieval.service import RetrievalService

# 在应用启动或组件初始化时创建，后续复用。
service = RetrievalService.from_config()

result = service.search(
    "借车发生事故，车主要承担责任吗？",
    top_k=5,
)
```

建议第一版注册工具名为 `search_statutes`，输入参数如下：

| 参数 | 类型 | 说明 |
|---|---|---|
| `query` | `str` | 必填，自然语言问题或法律名称＋条号 |
| `top_k` | `int` | 可选，默认 5，必须为正整数；工具侧上限待确认 |

检索模式、embedding 模型和融合参数由服务配置固定，第一版不向模型开放。`incident_date`、`dispute_type` 等参数尚无对应过滤实现，暂不加入工具参数。

## 3. 返回结构与状态映射

检索核心当前返回普通 `dict`，结构如下（字段值为示意）：

```python
{
    "status": "ok",
    "query": "原始查询",
    "results": [
        {
            "provision_id": "法条唯一标识",
            "law_title": "法律名称",
            "article_no": "条号",
            "hierarchy_path": ["章、节等层级"],
            "text": "完整法条正文",
            "source": {
                "url": "来源链接",
                "file": "来源 PDF 路径",
                "page_start": 1,
                "page_end": 1,
            },
            "version": {
                "publication_date": "YYYY-MM-DD",
                "effective_date": "YYYY-MM-DD",
                "expiry_date": None,
                "status": "登记的效力状态",
            },
            "retrieval": {
                "rank": 1,
                "score": 0.8,
                "matched_by": ["vector"],
            },
        }
    ],
    "warnings": [],
}
```

建议适配为 Runtime 的 `ToolResult`：

| 核心行为 | 工具状态 | 处理方式 |
|---|---|---|
| `status="ok"` | `ok` | 在 `data` 中保留查询、候选和警告，生成 `source_refs` |
| `status="no_results"` | `empty` | 保留空列表、`reason` 和 `warnings` |
| 抛出执行异常 | `error` | 按异常类型转换错误，保留必要诊断信息，避免暴露密钥 |

核心不会把执行异常包装为无结果。目前也没有经过验证的依据充分性判断，不自动输出 `insufficient`。

**语义约束：** `ok` 只表示存在候选；`score` 是排序分数，不是法律适用概率。精确路由、余弦检索与 RRF 的分数含义不同，不应直接跨通道比较。

## 4. Agent 需要了解的检索行为

- 明确的法律全称或简称＋中文/阿拉伯数字条号优先走精确路由，亦支持条号在法律名称之前的写法。
- 支持同法多条和跨法分别指定目标，按查询顺序去重返回全部指定条文，不因 `top_k` 截断。
- 任一指定目标未找到时，返回 `no_results`、`reason=exact_article_not_found`，警告列出缺失目标，不返回部分命中或相似条文。
- 条号范围、简写或法律与条号对应关系不明确时，返回 `no_results`、`reason=unsupported_exact_query`，提示逐条写明目标。
- 空白、仅符号输入，以及关键词无有效词或无正分候选，可以返回确定性空结果。
- 普通语义查询即使超出语料范围，也可能返回相似候选。Agent 仍需检查候选是否支持当前问题。
- 当前仅有单版本语料。登记为有效不代表已核验现行效力，也不代表适用于任意历史案件。

## 5. 分层与职责

```text
前端 → Agent / Runtime → search_statutes 薄适配 → RetrievalService
CLI / 检索评测 ───────────────────────────────→ RetrievalService
```

| 内容 | 建议负责方 |
|---|---|
| 资料解析、索引、召回、来源元数据 | RAG 侧 |
| 检索工具参数说明、结果包装和引用转换 | RAG 侧，结合 Runtime 类型实现 |
| 公共 ToolSpec / ToolContext / ToolResult、注册与调度 | Agent / Runtime 侧 |
| 查询组织、多争点拆分、继续检索或追问的决策 | Agent 侧 |
| 检索后依据充分性、答案生成 | Agent 侧；引用与答案核验需协同确认 |
| 运行状态、事件和前端交互接口 | Agent / Runtime 与 API 侧 |

适配层建议放在 `backend/tools/knowledge_tools.py`，仅做参数传递、结果包装与异常转换。检索核心不依赖 Runtime。跨模块模型可沿用团队契约中的 Pydantic 类型，具体导入路径以双方实际代码为准。

RAG 内部使用 LangChain 检索组件，不要求 Agent 主流程采用相同框架。CLI 继续作为独立调试入口，评测脚本用于批量验证检索效果。

## 6. 来源引用

建议以现有字段构造 `SourceRef`：

| SourceRef 字段 | RAG 字段或建议值 |
|---|---|
| `source_id` | `provision_id` |
| `source_type` | `"statute"` |
| `title` | `law_title` |
| `locator` | `article_no` |
| `version` | 版本标识的表达方式待确认，当前有日期和状态字段 |
| `excerpt` | `text`，第一版可保留完整法条 |
| `url` | `source.url` |

PDF 文件和页码可作为扩展字段保留，用于定位原文。`source.file` 是后端来源路径，若前端需要打开 PDF，需要 API 层提供可访问的文件地址。

最终答案中的引用应使用工具实际返回的 `source_id`，能回溯到对应条文。当前部分来源 URL 仍为官方数据库首页，具体版本链接需后续补齐。

## 7. 环境与运行管理

- 建议使用已验证的 `.venv-rag` 环境，依赖见根目录 `requirements.txt`。
- 默认向量模式依赖 `data/index/vector_metadata.npz`，查询缓存为 `embedding_cache.sqlite3`；关键词/混合模式还需 BM25 索引。
- BM25索引必须携带与当前词典、分词源码、jieba版本和配置匹配的`tokenizer_fingerprint`；无指纹旧索引或失配索引需通过`scripts/rag/build_bm25_index.py`重建。运行中修改词典/源码需先重启进程；默认向量通道不依赖此索引。
- `.venv-rag/`、`.env`、`data/index/` 不随 Git 分发，需要约定本地环境和索引的准备方式。
- 环境变量见 `.env.example`：`SILICONFLOW_API_KEY`、`RAG_EMBEDDING_BASE_URL`、`RAG_EMBEDDING_MODEL`。查询模型与端点必须匹配索引。
- 服务初始化后复用已加载资源。普通查询缓存未命中时会请求 embedding API，延迟与网络有关。
- embedding 客户端默认单次 HTTP 超时为 30 秒，与 Runtime 的 `ToolSpec.timeout_s` 默认值一致；通过构造参数 `timeout_s` 或 `RAG_EMBEDDING_TIMEOUT_S` 可配置。客户端不自动重试，由 Runtime 按 `ToolSpec.max_retries` 决定是否重新调用工具，独立脚本失败后由调用方决定是否重跑。
- 检索入口保持 `RetrievalService.search(query, top_k=5)`，当前不增加 `deadline` 参数；后续确有需要再扩展。HTTP 请求超时抛出 `TimeoutError`，不包装为无结果。未来工具适配需将其转换为 `TIMEOUT` 并按约定标记可重试；目前尚未实现该适配。
- Runtime 放弃等待不等于底层请求已取消。urllib 的 HTTP 超时是阻塞操作超时，不保证整个工具调用在 30 秒内强制结束；执行取消和跨重试的总预算由 Runtime 侧后续确认。

## 8. 首轮联调

先验证调用链与结果语义，再验证真实模型的工具调用和回答表现。

| 场景 | 重点检查 |
|---|---|
| 普通法律问题 | 工具可调用，候选正文与来源完整 |
| 法律名称＋中文条号 | 精确命中指定条文 |
| 同法多条、跨法多条、阿拉伯条号及反向全称 | 完整定位，按查询顺序去重，不受top_k截断 |
| 部分目标缺失或不支持的复合写法 | 返回empty并保留缺失或格式原因，不静默返回首条 |
| 指定但未找到的条号 | 映射为 `empty`，保留原因，不返回相似条文替代 |
| 空白、符号或非法参数 | 参数校验和空结果处理清晰 |
| 超出语料覆盖范围 | Agent 不把相似候选自动视为充分依据 |
| API 失败或索引缺失 | 识别为执行故障，不混作无依据 |
| 生成带引用的答案 | 引用 ID 对应实际候选，能定位原文 |

现有检索评测只能说明候选召回与排序表现，尚不能证明 Agent 的答案和引用正确性。双方应共同选少量端到端样例验证这些行为。

## 9. 本次需要确认的事项

1. 第一版是否采用 `search_statutes(query, top_k=5)`，以及 `top_k` 的上限。
2. Runtime 公共类型、注册方式和工具适配层的实现分工。
3. `ToolResult.data` 的候选存放方式，以及 `SourceRef` 的版本与页码字段。
4. 索引准备方式、初始化时机、超时预算与重试归属。
5. 首轮联调样例及双方接入顺序。

上述事项确认后即可实现薄适配并联调；日期过滤、reranker、多版本管理等增强另行讨论。
