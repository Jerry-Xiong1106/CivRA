# Agent 执行层

2026-11-10：模型与工具循环已迁移到 LangChain create_agent。公共 start_run、resume_run、cancel_run、轨迹导出和RAG接口继续沿用。

## 当前执行路径

用户输入 → create_agent → ChatOpenAI → 工具函数 → registry → 业务服务。
模型/工具的反复调用和完整消息历史由框架管理。runtime/middleware.py直接实现项目钩子，runtime/store.py统一记录步骤；没有额外的execution转发层。

工具回复中的引用只保存在data.matches[].source_ref，正文只保存在match.text。Runtime从这些匹配项派生轨迹和最终答案的source_refs，不再向模型重复发送顶层来源列表、excerpt或重复计时meta。

| 文件 | 职责 |
|---|---|
| factory.py | 创建统一ChatOpenAI模型、工具注册表与AgentRunner |
| runner.py | 生命周期接口与create_agent组装 |
| case_port.py | 案件读取/阶段切换接口与内存实现 |
| model/llm.py | 模型构造、回复/用量序列化、ScriptedLLM测试模型 |
| model/prompts.py | 原生消息上的系统提示、案件状态和上下文压缩 |
| runtime/middleware.py | 框架钩子、工具适配、案件刷新和追问/结束协调 |
| runtime/builtins.py | ask_user、路线选择、finish的参数说明 |
| runtime/state.py | 项目运行元数据、预算判断和最终输出规则 |
| runtime/store.py | 框架检查点、元数据、步骤/事件记录和用量累计 |

入口保留在agent顶层；模型相关内容集中在model/，执行和存储集中在runtime/。项目内调用方已更新导入路径，不保留旧路径转发文件。

重复的lc_model.py、lc_tools.py和原生HTTP客户端已移除。评测ModelAdapter也使用同一个BaseChatModel对象；Direct/RAG基线仍只生成一次，不获得Agent动态工具能力。

## 配置与运行

~~~powershell
.\.venv-rag\Scripts\python.exe -m pip install -r requirements.txt
.\.venv-rag\Scripts\python.exe -B -m pytest tests -q -p no:cacheprovider
.\.venv-rag\Scripts\python.exe scripts/agent/run_agent.py --dry-run --mock-statutes
~~~

真实模型读取SILICONFLOW_API_KEY或CIVRA_LLM_API_KEY，可通过CIVRA_LLM_MODEL和CIVRA_LLM_BASE_URL选择OpenAI兼容模型服务。模型max_retries固定为0。旧CIVRA_LLM_BACKEND=native已移除，请从本地配置删除该选项。

`.env`统一由`backend.common.config.load_dotenv`加载；factory和CLI在入口调用，EmbeddingClient构造函数只接收显式参数，`from_env()`读取已加载的环境。直接使用检索服务的调用方需要自行加载环境。评测计划转换函数位于`evaluation/config.py`。

真实法规检索默认exact-vector＋metadata，需要准备vector_metadata.npz；已有索引可直接复用。未缓存的问题向量需要embedding API。--dry-run只替换生成模型，完全离线还需--mock-statutes或显式使用离线检索配置。

~~~python
from backend.agent.factory import build_runner

setup = build_runner(retrieval_service=service)  # 可注入已初始化的RAG服务
setup.cases.create("case-1", {"facts": {}})
info = setup.runner.start_run("case-1", "我的车被追尾了")
~~~

knowledge_tools.py仍负责法条结果转换，registry.py仍负责业务工具校验、阶段、超时和重试。工具函数直接由create_agent使用，不再经过独立LangChain工具包装文件。真实业务能力未交付时仍可显式使用mock；mock结果不能作为业务核验结论。

factory从空注册表按配置装配工具。`update_case`继续位于mock_tools.py并使用内存存储；关闭mock_business_tools只移除模拟核验、证据检查和文书生成。注册表由创建方通过`close()`或上下文管理器释放，CLI在退出时关闭。

普通工具和内置工具共用参数、阶段校验。有副作用的工具不自动重试；只读工具仅在可重试错误且deadline仍有余量时重试。Agent为一次逻辑工具调用分配timeout_s总时限，所有尝试共用；检索会将deadline传递给embedding HTTP请求。超时不代表工作线程已被强制终止。

检索资源加载、一致性校验、manifest和retriever创建位于`knowledge/retrieval/resources.py`；RetrievalService负责查询路由，保留原有索引注入和from_config入口。

## 顺序、等待与恢复

Runner使用同步invoke及max_concurrency=1，让框架按模型返回顺序执行同轮工具，无需手写条件锁和游标。遇ask_user或finish，middleware让后续工具返回未执行回执，不发生后续写入。此配置针对当前同步执行入口，不能直接套用于异步ainvoke。

ask_user结束当次框架调用并保存同一thread_id。resume_run先验证wait_id、案件版本和重复request_id，再向同一框架线程追加用户回复。框架负责恢复完整消息历史，middleware每次调用模型前重新读取案件状态。

~~~text
var/runs/
├── agent.sqlite3           # LangGraph SQLite检查点，按run_id/thread_id隔离
└── run_id/
    ├── checkpoint.json     # 项目状态、预算、wait和引用，不再重复保存消息历史
    ├── cancel.requested    # 收到取消请求时创建，独立于checkpoint保存
    ├── trace.jsonl
    ├── events.jsonl
    └── results/step_NNNN.json
~~~

SQLite与JSON元数据需要一起保留；缺少框架检查点时拒绝继续，不能从头重跑已执行工具。案件数据本身仍由CaseStatePort提供，当前演示使用InMemoryCaseStore。

完整历史通过RunStore.read_messages(run_id)按需从框架检查点读取。旧v2元数据中的messages镜像会被忽略，后续保存时不再写入；恢复始终依赖同一LangGraph线程。提示压缩直接处理原生消息，并保留消息ID及附加字段。取消信号由共享RunStore读取，另一Runner实例也可请求取消，保存旧checkpoint不会覆盖取消标记。

事件和轨迹JSONL按追加方式保存。RunStore为最近访问的日志维护内存字节偏移索引：首次访问扫描文件，后续只扫描新增的完整行，并定位读取所需事件或分页记录；末行尚未写完时留到下次读取。索引最多缓存128个文件，淘汰后可重建，不增加持久化副本。CLI直接按轨迹游标展示新步骤。

普通步骤结束时保存中间checkpoint。运行进入终态后，Runner统一更新总耗时、保存最终checkpoint，再发布终态事件；等待态取消由同一store.end入口立即提交。完整模型请求快照和终态事件中的final仍保留。上下文压缩在单次调用内缓存消息长度，删除旧轮次时只扣减长度，不重复序列化剩余消息。

新运行标记engine=langchain-create-agent-v2。当前模型不再保留旧version/excerpt或字符串定位；旧格式记录不进入运行管理或恢复。历史原始文件不改动，需按新格式重新开始。迁移前工作区已另存Git备份。

## 保留的项目规则

- 案件版本、路线选择、暂停恢复、请求去重。
- 步数、模型调用、工具调用、Token和活动时间预算；检索—核验反馈轮数上限。
- 不修改存储历史，仅压缩发给模型的历史视图。
- 实际调用轨迹、来源引用、用量未知时的null和脱敏错误。
- 取消后在下一执行边界停止；正在等待的网络线程不能强制终止。

固定依赖中包含langchain、langgraph和SQLite checkpoint包。真实模型/embedding效果仍需另行验证；离线脚本和本地HTTP测试只能证明调用链与规则符合预期。

前一轮迁移在.venv-rag固定依赖环境验证：旧引用兼容删除后99项测试及4项子测试通过，pip check通过；另以两个独立Python进程验证等待恢复，并用真实法条向量索引和只读查询缓存跑通检索、引用及快照链路。2026-11-11职责拆分按步骤运行定向轻量测试，没有重跑全量测试或请求真实生成/embedding API。

## 尚待业务联调

成员3真实业务工具、案例检索、每份业务产物的版本过期标记及法律依据支持核验仍待接入。此前旧实现的DeepSeek试运行观察不能作为新实现的效果报告，也不能因迁移框架就宣称引用或法律结论得到改善。
