# LangChain迁移与整理验证

日期：2026-11-04（北京时间）。原生检索基线为`fb42e9f`中的`aa73532`检索实现；本文记录迁移和整理后的实现及验证结果。默认保持精确路由＋metadata向量，没有新增重排、改写或拒答阈值。

## 组件与环境

Document保存索引文本和完整法条记录；metadata输入显式拼接法律名称、层级路径和正文，引用保留原文。EmbeddingClient实现Embeddings并复用原SQLite缓存。InMemoryVectorStore实际执行余弦检索；community BM25Retriever注入原始jieba分词，派生类仅附评分与正分过滤。RunnableParallel组织通道，RRF公式仍保留法条ID去重及通道诊断。

验证环境为隔离的`.venv-rag`、Windows x64/Python 3.12，core 1.7.4、community 0.4.2；当时解析的Pydantic为2.13.5。`pip check`通过。直接依赖集中在根目录requirements.txt，Pydantic由LangChain间接引入；不保留整份机器环境的pip-freeze快照。community有维护收缩提示，直接使用隔离在关键词模块。

## 保留的评测证据

原40题的题库和三组关键词/十组完整基线报告继续保留在上级目录；24题首次评测的题库、完整结果、冻结清单和分析继续保留在[holdout_v1](../holdout_v1/report.md)。本目录只保留本报告与[comparison.json](comparison.json)，不重复存放三份基本相同的迁移全量结果。

comparison.json包含23组配置的指标前后值、article/non_article分组、变化题的前五排名、共同候选最大分数差，以及各次运行的依赖、配置和代码/输入哈希。retained_run_manifests保留被删副本的哈希用于核对记录；需要完整当前输出时运行现有评测CLI，显式另存，不覆盖原始基线。

## 实测结果

旧40题执行三组关键词和十组完整配置，24题执行十组完整配置，共23组、760条逐题返回。迁移时所有Hit@1/3/5、Recall@1/3/5、MRR@5及article/non_article汇总均与原生基线一致。

| 默认方案统计范围 | 有依据题数 | Hit@1 | Hit@5 | Recall@5 |
|---|---:|---:|---:|---:|
| 旧40题开发集 | 36 | 94.44% | 97.22% | 95.83% |
| 新24题首次评测集 | 20 | 95.00% | 100.00% | 97.50% |

向量前五法条及顺序不变，共同候选分数最大变化小于2.2×10^-7。BM25原始共同候选分数不变，但社区组件默认get_top_n平局顺序与旧稳定排序不同：旧题正文BM25有4题变化、metadata有3题、带精确路由时2题；新题仅无路由BM25有1题变化。此变化传递到部分RRF排名或分数，最大融合分数差约0.0002561；新24题RRF前五顺序不变。接受并记录组件默认行为，不恢复另一条旧算法旁路。

第005题效力规则排序、第020题跨法第二争点缺失等不足仍存在。两套题库的4道语料不足题均返回候选；本层未新增语义拒答。题目已被查看与评分，当前只用于回归及框架迁移对照，不是新的外部盲测。

## 代码整理与验证范围

统一入口为retrieval/service.py；keyword/bm25_retriever.py和vector/vector_retriever.py创建并执行各通道；retrieval/fusion.py组织融合。索引类只负责构建、校验和存取，不保存Retriever运行对象。BM25持久化类路径未改，旧pickle无需重建。

整理后23组、760条返回与整理前LangChain版本的排名、分数、指标及配置完全一致；只更新时间和代码路径/哈希。迁移/整理过程使用缓存并禁止意外embedding请求，无新增API请求，法条索引、缓存、原题库与历史报告字节未变。

当前保留5项检索组件测试：Document与metadata拼接、缓存接口、精确/空结果、BM25正分/索引往返/RRF、向量评分及并发top-k隔离。已撤下的适配功能不再计入当前测试数量。运行：

```powershell
& .\.venv-rag\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
& .\.venv-rag\Scripts\python.exe -m pip check
```

## 重跑当前实现

在仓库根目录执行，输出写临时目录：

```powershell
$ragPython = ".\.venv-rag\Scripts\python.exe"
& $ragPython scripts/rag/evaluate_retrieval.py --output "$env:TEMP/civra_lc_keyword.json"
& $ragPython scripts/rag/evaluate_retrieval.py --mode all --output "$env:TEMP/civra_lc_dev.json"
& $ragPython scripts/rag/evaluate_retrieval.py --mode all `
  --dataset evaluation/rag/holdout_v1/queries.jsonl --output "$env:TEMP/civra_lc_holdout.json"
```

安装与查询方法集中于[向量报告](../../../docs/rag/rag_vector_baseline.md#配置构建与查询)。当前仅维护Python检索服务与CLI，Agent接入后续另行设计。
