# 向量与融合检索基线

通过SiliconFlow `/v1/embeddings` 使用 `BAAI/bge-m3` 的1024维dense embedding。未接入BGE-M3稀疏或多向量输出，无需本地GPU或Milvus。当前只有1690条法条，归一化向量后对全部条文做余弦精确排序。

## 配置、构建与查询

本地 `.env` 填写以下字段，系统环境变量优先；`.env.example`只保留空密钥模板。`.env`和 `data/index/`均被Git忽略，密钥不写入代码、报告或索引。

```dotenv
SILICONFLOW_API_KEY=你的密钥
RAG_EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1
RAG_EMBEDDING_MODEL=BAAI/bge-m3
```

在仓库根目录执行：

```powershell
$ragPython = "C:\Users\Miller\AppData\Local\Programs\Python\Python312\python.exe"
& $ragPython scripts/rag/build_bm25_index.py
& $ragPython scripts/rag/build_vector_index.py --variant both --batch-size 32
& $ragPython scripts/rag/evaluate_retrieval.py --mode all
& $ragPython scripts/rag/search.py "把车借给朋友开，结果出了事故，车主要不要承担责任？"
& $ragPython scripts/rag/search.py "把车借给朋友开，结果出了事故，车主要不要承担责任？" --mode hybrid
& $ragPython scripts/rag/search.py "把车借给朋友开，结果出了事故，车主要不要承担责任？" --mode vector --vector-index data/index/vector_text.npz
```

统一入口只有 `scripts/rag/search.py`，默认 `--mode exact-vector` 与metadata向量；可切换keyword（精确路由＋BM25）、vector（纯向量）、exact-vector（精确路由＋向量）、hybrid（精确路由＋RRF）。旧重复查询脚本已删除。`--top-k`控制最终结果数。

索引构建支持 `--variant text|metadata|both`，默认both。text只送正文，metadata将“法律名称＋层级路径＋正文”拼成一段文本送embedding API。条号、时间及来源不拼入。问题直接用原文本生成向量，不附指令，不裁剪维度。原文、页码和版本随索引保存，用于结果引用。

- `data/index/vector_text.npz`、`vector_metadata.npz`：两套法条索引，向量与法条顺序保存在同一个非pickle文件；另存模型、endpoint、输入配置及语料哈希。
- `data/index/embedding_cache.sqlite3`：按endpoint、模型、完整输入哈希缓存向量，成功批次即时提交，重复构建和已有问题可复用。新问题需要API请求，索引本地相似度计算不调用API。
- `data/index/bm25.pkl`：原始jieba基线的关键词索引，供keyword和hybrid模式读取。

查询模型/endpoint不匹配、融合语料不一致时会报错。法条改变后仍需重建索引；缓存复用不等于增量版本更新。服务端模型名不保证永久固定版本，因此不承诺跨时间位级复现。

## RRF与共享逻辑

关键词、向量、融合共用 `backend/knowledge/retrieval/search.py` 的精确路由与结果格式；明确法律名称和条号命中时返回单条条文并跳过embedding。未命中则运行相应召回通道，当前不处理一问多条号或阿拉伯条号规范化。

RRF默认每通道召回50条、等权、`rrf_k=60`；分数是各通道 `1/(60+rank)` 之和，不直接相加BM25与余弦分数。按provision_id去重，每通道重复ID只算一次。平局按最佳通道排名、法条ID排序。`retrieval.channels`记录原始排名、原始分数及融合贡献；score_type区分exact与rrf。`--candidate-k`和`--rrf-k`可显式设置，本轮未扫描这些参数。

评测统一为 `evaluate_retrieval.py`：默认只评关键词，`--mode all` 加载两套向量并评全部十种配置；两种模式按原路径分别输出关键词和向量JSON报告。评测与实际查询调用同一条向量/融合代码路径，传入已有查询向量，不另写一套精确匹配或排序。检索代码不读取gold。关键词通道已回滚实验预处理并重建索引，本轮未新增embedding请求。

共享语料读取和metadata拼接位于 `backend/knowledge/corpus.py`；`keyword/`放BM25、jieba分词和词典，`vector/`放embedding客户端及向量索引，`retrieval/`放精确路由和融合。纯向量CLI不加载jieba，keyword模式不实例化embedding客户端或读取API密钥。目录迁移后BM25 pickle需重新构建，本地索引已重建；NPZ向量和SQLite缓存不保存这些Python模块路径，因此无需重新请求API。

## 当前开发集结果

题库40题：36题有参考依据、4题为反例。两套向量输入、精确路由和融合共10种配置，结果及哈希保存到 `evaluation/rag/rag_vector_baseline.json`，定义与题库修订见 [关键词报告](rag_keyword_baseline.md)。全部36题：

| 配置 | Hit@1 | Hit@3 | Hit@5 | Recall@5 | MRR@5 |
|---|---:|---:|---:|---:|---:|
| BM25，名称＋路径＋正文 | 69.4% | 80.6% | 83.3% | 80.6% | 0.7463 |
| 精确路由＋BM25 | 80.6% | 88.9% | 88.9% | 86.1% | 0.8380 |
| 向量，仅正文 | 83.3% | 88.9% | 91.7% | 90.3% | 0.8681 |
| 向量，metadata | 86.1% | 88.9% | 91.7% | 90.3% | 0.8806 |
| 精确路由＋向量，仅正文 | 91.7% | 97.2% | 97.2% | 95.8% | 0.9444 |
| 精确路由＋向量，metadata | 94.4% | 97.2% | 97.2% | 95.8% | 0.9583 |
| RRF，仅正文向量 | 77.8% | 91.7% | 91.7% | 87.5% | 0.8426 |
| RRF，metadata向量 | 80.6% | 88.9% | 91.7% | 87.5% | 0.8542 |
| 精确路由＋RRF，仅正文向量 | 86.1% | 97.2% | 97.2% | 93.1% | 0.9120 |
| 精确路由＋RRF，metadata向量 | 88.9% | 94.4% | 97.2% | 93.1% | 0.9236 |

排除5道条号题后的31题：

| 配置 | Hit@1 | Hit@3 | Hit@5 | Recall@5 | MRR@5 |
|---|---:|---:|---:|---:|---:|
| BM25，名称＋路径＋正文 | 77.4% | 87.1% | 87.1% | 83.9% | 0.8118 |
| 向量，仅正文 | 90.3% | 96.8% | 96.8% | 95.2% | 0.9355 |
| 向量，metadata | 93.5% | 96.8% | 96.8% | 95.2% | 0.9516 |
| RRF，仅正文向量 | 83.9% | 96.8% | 96.8% | 91.9% | 0.8978 |
| RRF，metadata向量 | 87.1% | 93.5% | 96.8% | 91.9% | 0.9113 |

31题不触发精确路由，带路由时指标相同；条号题保留作功能回归，反例另报。默认metadata向量＋精确路由在全部36题上Hit@1为94.4%、Recall@5为95.8%，优于metadata RRF＋精确路由的88.9%、93.1%。当前不将等权RRF设为默认，保留它作为可切换的基线。

逐题看，metadata向量RRF补回第33题原告死亡的终结诉讼依据，却使第30题借车责任跌出前五，并使第6、21题各漏一条gold。metadata向量＋精确路由仍漏第33题，并在第15题漏交叉引用第510条。上述差异说明通道互补不保证融合排序全面提升。

## 可用范围与限制

默认方案在31道非条号题上Hit@1/3/5为93.5%/96.8%/96.8%，Recall@1/3/5为88.7%/95.2%/95.2%。适合原型联调，将候选原文、来源和版本作为后续推理输入；不足以单凭分数认定可靠上线或保证法律结论正确。

Hit不衡量依据集合是否完整，Recall不衡量误召回数量、案件事实理解、日期适用或最终生成质量。10种配置对4道反例均返回候选，仍无可靠拒答；元数据/时间过滤及Agent工具集成未完成。

回滚BM25调优并不消除开发集已用于方案选择的事实，向量输入和默认入口也参考了这份题库。当前分数仍是开发结果，不能作为独立测试效果。下一步在固定实现后补充未参与调优的新题与人工标注，再检查下游答案与引用质量。
