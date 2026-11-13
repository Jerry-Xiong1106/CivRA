# RAG 接入与评测说明

更新：2026-11-11。适配已迁到 feature/agent-factory 的现有 knowledge_tools.py；检索修复由 origin/develop 同步。评测人员对返回格式的正式验收仍待完成。

## 1. 当前范围

- 三部法律共1690条；默认精确条号路由＋metadata向量检索，top_k默认5。
- query/top_k是实际工具的全部参数。案例检索、日期/法域过滤、get_source、多版本检索和reranker延期。
- 原始调用仍为 RetrievalService.search()。没有 search_matches()、filters或deadline查询入口。
- 工具只返回候选，不判断法律依据是否充分，不认定案件事实或生成文书。
- 真实适配集中于 backend/tools/knowledge_tools.py；mock_tools.py继续提供模拟工具和演示业务工具。LangChain工具包装仍经过现有注册表。

## 2. 直接调用与工具接入

直接检索、评测可继续使用原接口：

~~~python
from backend.knowledge.retrieval.service import RetrievalService
from backend.knowledge.retrieval.results import to_search_match

service = RetrievalService.from_config()
raw = service.search("民法典第一条和第二条", top_k=1)
matches = [to_search_match(hit, service.source_metadata) for hit in raw["results"]]
# 精确指定的两条全部返回，top_k不截断明确目标。
manifest = service.knowledge_manifest  # 初始化后由评测程序保存一次
~~~

通过现有factory接入Agent：

~~~python
from backend.agent.factory import build_runner

setup = build_runner(retrieval_service=service)
setup.cases.create("case-1", {"facts": {}})
# setup.runner.start_run("case-1", "民法典第一条规定什么？")
~~~

也可以只注册检索工具，不启动Agent：

~~~python
from backend.tools.registry import ToolRegistry
from backend.tools.knowledge_tools import register_knowledge_tools

registry = ToolRegistry()
register_knowledge_tools(registry, retrieval_service=service)
# execution = registry.execute("search_statutes", {"query": "民法典第一条"}, ctx)
# result = execution.result
~~~

不传service时，真实工具在第一次调用时懒加载服务，后续复用。statute_search/search函数注入保留给测试；它与retrieval_service不能同时提供。只有裸回调、没有服务来源信息时，knowledge_version和未知获取日期返回null，不伪造快照。

factory默认注册真实search_statutes，其他成员的业务工具仍可为mock；mock_business_tools=False可关闭这些模拟业务工具。这不代表真实Agent已经通过法律效果验收。

## 3. 返回与错误

原search()保留status/query/results/warnings和必要的reason。工具适配负责整理ToolResult：

| 情况 | 工具状态 | 返回内容 |
|---|---|---|
| 有候选 | ok | data.matches，每条source_ref是工具载荷中唯一的引用位置 |
| 指定条文不存在或普通检索无候选 | empty | matches=[]，empty_reason及warnings |
| 空白/符号查询、不支持的精确写法、非法参数 | error / INVALID_ARGS | 保留原因，不当作语料缺失 |
| HTTP超时 | error / TIMEOUT | 可由Runtime重试 |
| embedding上游失败 | error / UPSTREAM | 429、500、502、503、504及连接失败可重试，401等不重试 |
| 索引/来源文件缺失 | error / UPSTREAM | 提示准备对应索引 |
| 其他执行故障 | error / INTERNAL | 脱敏错误，不返回替代候选 |

payload包含schema_version=0.1、query、matches、empty_reason、warnings、knowledge_version。每个match包含text、source_ref、rank、score、score_type、retrieval和hierarchy_path。引用仅在matches[].source_ref中，正文仅在matches[].text中，不输出顶层source_refs或重复hits。rank/score/score_type只保留在match顶层，retrieval保留matched_by、通道诊断等其他字段。

重复的工具meta计时、固定attempt_count和implementation_version已移除。Runtime以TraceStep.duration_ms计时，以Execution.attempts/RunUsage记录含重试的执行次数；单次RAG基线另由评测程序记录retrieval_ms。这些计数不是embedding HTTP请求数，未测量的用量和费用保持未知。

Runtime的ToolContext保持原样；RAG不接收deadline。embedding单次默认30秒且无内部重试。Runtime停止等待仍不等于终止底层线程，不能声称已有跨重试的严格总截止时间。

## 4. 引用结构

公共类型沿用schemas.py。SourceRef只接受结构化SourceLocator，source_version为必填字符串，不保留旧字段或字符串定位解析。

| 字段 | 约定 |
|---|---|
| source_id | 原有provision_id |
| source_type | statute |
| title | 法律名称 |
| locator | 法规返回article、page_start、page_end；不输出未使用的paragraph/chunk_id |
| source_version | 法律公布日期；未知时用条文正文SHA-256 |
| effective_from / effective_to | 生效/失效日期，未知null |
| retrieved_at / retrieved_date | 现有登记只有日期，填retrieved_date，时间戳为null |
| content_sha256 | 返回正文的SHA-256 |
| source_file / url | 原PDF文件名和来源URL |

SourceLocator至少有一种位置，页码必须为正且起止有序。来源版本只有source_version；version、excerpt及字符串locator会被明确拒绝。

to_search_match只映射JSON字段，不先构造模型再转回字典。Runtime从matches校验一次并收集SourceRef；无效引用转为INTERNAL，不进入模型上下文或最终来源。执行轨迹、检查点和最终答案中的source_refs是派生的运行级汇总，不是工具的第二份引用载荷，也不复制正文。mock法规检索复用相同工具参数和转换函数。

可复现的引用样例：

~~~python
hit = service.search("民法典第一条")
ref = to_search_match(hit["results"][0], service.source_metadata)["source_ref"]
assert ref["source_id"] == "中华人民共和国民法典::2020-05-28::第一条"
assert ref["source_version"] == "2020-05-28"
assert ref["locator"]["page_start"] == 7
assert ref["retrieved_at"] is None and ref["retrieved_date"] == "2026-10-30"
empty = service.search("民法典第9000条")
assert empty["reason"] == "exact_article_not_found"
~~~

最终答案应引用实际返回的source_id。部分URL仍是官方数据库首页，具体原文可按PDF文件、页码和条号核对。结构化引用不保证模型最终会正确标注或解释依据，仍需输出核验。

## 5. 快照与评测衔接

knowledge_version是实际加载法条按ID排序并规范JSON序列化后的SHA-256。它标识法条记录，与top_k和检索模式无关。knowledge_manifest另外记录各法律条数、向量配置/向量哈希、BM25版本/分词指纹/参数和检索配置。

这些是内存属性，不自动复制或保存语料。评测程序在实验开始时核对并保存manifest及对应JSONL、PDF和索引；不增加请求级knowledge_version校验或独立manifest工具。工具结果中携带knowledge_version，便于关联记录。

当前只索引法规，case_exclusion_manifest为null不代表已完成最终测试排除审计。现有40/24题用于检索开发和回归，不能直接充当端到端最终测试。

评测人员可以直接适配search()原结果，也可读取真实工具的data.matches。request_id、experiment_run_id、评测ok/error外壳和评分记录由evaluation/adapters衔接。当前RetrievalAdapter也复用to_search_match，统一结构化source_ref和公布日期source_version；历史试点仍保留原载荷，不覆盖或混入新结果。

## 6. 环境与模式

默认CIVRA_RETRIEVAL_MODE=exact-vector，使用data/index/vector_metadata.npz；索引已有时直接复用。语义查询缓存未命中会请求SiliconFlow BAAI/bge-m3 API，法条向量不重新生成；精确路由不请求embedding。

离线关键词检查可显式选择CIVRA_RETRIEVAL_MODE=keyword或注入RetrievalService(RetrievalConfig(channel="keyword"), ...)。这是测试配置，不改变默认方案。CIVRA_RETRIEVAL_MODE只控制工具默认懒加载，显式注入service时以其配置为准。

.env、.venv-rag及data/index不提交Git。CLI按当前检索模式检查所需索引，不再仅以BM25文件判断向量模式能否使用。--dry-run只替换生成模型；完全离线需同时使用--mock-statutes，或显式使用离线检索配置。

## 7. 联调与验证

首轮检查普通候选、精确多条、指定条文缺失、非法参数、模拟超时与上游故障，以及结构化引用在工具、LangChain包装和检查点间的传递。保留现有测试，临时验证脚本用完删除；不以脚本模型结果宣称真实模型效果。

2026-11-10本地验证：系统Python运行现有88项测试全部通过；.venv-rag使用本地只读查询缓存和真实向量索引，对64道既有题逐条核对正文、排名、分数和引用，全部一致。另验证默认exact-vector懒加载只初始化一次、service注入、多条号不截断、非法参数拒绝、超时/上游错误重试及factory脚本Agent的检查点往返。没有请求真实embedding/生成API，未重建索引或写入查询缓存，临时验证未保留文件。系统Python的LangChain版本与requirements固定版本不同，本结果不等同于固定依赖环境的完整验收。

正式评测仍需共同确认接收格式、固定检索配置、缓存冷热、计时/重试规则和团队标注复核。get_source、案例检索、日期过滤等延期项不因接口接通而视为完成。

同日Agent执行层已改为create_agent，RAG的search、工具注册和matches/source_ref合同不变。新运行恢复需保留var/runs/agent.sqlite3和项目JSON元数据；旧版未完成会话重新开始，旧原始文件不改动，当前接口不再兼容解析旧引用。离线ScriptedLLM与真实模型共用同一框架路径，原生HTTP客户端和独立lc_tools/lc_model包装已移除；历史试点结果未覆盖，本次没有新增真实模型效果结论。

create_agent迁移最终验收使用.venv-rag的固定依赖：95项测试及4项子测试通过，pip check无冲突。两个独立进程在重建相同案件状态后成功恢复同一框架线程；真实向量RAG在只读问题缓存下通过新Agent及引用链路。此前系统Python的88项结果为迁移前适配记录，不作为本轮固定依赖验收结论。

2026-11-11引用去重后97项测试及4项子测试通过；另以真实缓存向量核对新工具载荷只保留一份引用和正文，轨迹/最终来源汇总、引用校验及检索分数排序均保持正确。临时验证不留脚本，不请求真实API。
2026-11-11进一步删除旧引用模型字段与同步校验，运行记录使用langchain-create-agent-v2。当前运行管理跳过旧格式记录，不尝试转换旧引用，旧文件不删除。
