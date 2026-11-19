# Agent 执行层

2026-11-16：工具定义迁移为 LangChain 原生 `@tool`，`create_agent` 直接接收工具列表。公共 start_run、resume_run、cancel_run、轨迹导出和RAG查询接口继续沿用。

## 当前执行路径

用户输入 → create_agent/ChatOpenAI → middleware → LangChain ToolNode → 原生工具函数 → 业务服务。
模型/工具循环和完整消息历史由框架管理。`runtime/middleware.py`统一处理阶段、预算、上下文和轨迹；`runtime/execution.py`仅执行超时和重试策略，通过框架提供的handler运行工具，不进行工具注册或名称分发。原`tools/registry.py`和`framework_tool`转换层已移除。

工具回复中的引用只保存在data.matches[].source_ref，正文只保存在match.text。Runtime从这些匹配项派生轨迹和最终答案的source_refs，不再向模型重复发送顶层来源列表、excerpt或重复计时meta。

| 文件 | 职责 |
|---|---|
| factory.py | 创建统一ChatOpenAI模型、原生工具列表与AgentRunner |
| runner.py | 生命周期接口与create_agent组装 |
| case_port.py | 案件读取/阶段切换接口与内存实现 |
| model/llm.py | 模型构造、回复/用量序列化、ScriptedLLM测试模型 |
| model/prompts.py | 原生消息上的系统提示、案件状态和上下文压缩 |
| runtime/middleware.py | 框架钩子、工具适配、案件刷新和追问/结束协调 |
| runtime/execution.py | 一次drive内的执行线程、共同截止时间、重试及通用错误处理 |
| runtime/builtins.py | 原生ask_user、路线选择、finish工具 |
| runtime/state.py | 项目运行元数据、预算判断和最终输出规则 |
| runtime/store.py | 框架检查点、元数据、步骤/事件记录和用量累计 |

入口保留在agent顶层；模型相关内容集中在model/，执行和存储集中在runtime/。项目内调用方已更新导入路径，不保留旧路径转发文件。

重复的lc_model.py、lc_tools.py和原生HTTP客户端已移除。评测ModelAdapter也使用同一个BaseChatModel对象；Direct/RAG基线仍只生成一次，不获得Agent动态工具能力。

## 配置与运行

人工测试入口已集中到`scripts/agent/interactive.py`（默认空白案件）；两路由真实模型验收使用`scripts/agent/smoke_routes.py`。旧`var/ai-smoke-20261115/interactive.py`等一次性脚本已清理，历史结果保留。测试分组及命令见`tests/README.md`。

2026-11-17两条主路由：`RunConfig.conversation_route`默认`auto`，新运行先通过同一模型的一次无工具JSON分类，区分`consultation`（咨询解答）和`case_work`（案件办理）。一般规则、概念及明确限定的解释使用咨询入口；用户描述现实纠纷并希望处理、追讨或了解眼前怎么办，即进入办理，不要求知道工具名称。明确请求检查材料、计算、比较路线或起草也属办理。`agent/routing.py`维护语义分类说明，无关键词规则。前端明确按钮可传`conversation_route="case_work"`跳过分类，咨询入口可显式传`consultation`。

咨询入口开放法条检索、空白原表查询、必要追问、路线选择及结束，不能更新事实或生成成果；办理入口使用案件阶段和enabled_tools允许的业务工具。分类失败回退咨询，记录原因，不开放额外业务能力。分类调用及用量计入主模型预算和轨迹（purpose=conversation_routing）；类型及依据保存在检查点，`RunInfo.conversation_route`用于展示。追问回复不会重新分类。若咨询过程中用户明确在菜单选定路线，程序保存路线并将入口提升为案件办理；路线选择和生成办理清单仍是操作事件，没有route_choice/route_plan两个分类。新问题开始新运行并重新识别，不因案件已选诉讼就把所有后续问题当作办理。

额度检查和动态计数整理位于 `runtime/middleware.py`。`RunConfig.max_search_calls=3`、`max_clarification_rounds=3`、`max_questions_per_round=2`适用于所有输入，包括材料/文书操作；不存在未匹配到任务就放宽限制的回退。前两个计数上限可设为None，但仍受全局预算限制。精确重复的追问会拒绝，语义改写的重复主要由提示约束。追问必要性和是否需要比较路线由当前用户目标及上下文判断，程序不按关键词替用户决定。路线比较允许依据和费用计算为空，未知项保留；自然语言明确选路线目前仍通过菜单提交确认，未实现直接提交动作。

已检索次数、追问轮数、问题记录和本轮是否选过路线保存在检查点，resume不重置检索或追问额度。用户选路线不消耗事实追问额度，额度耗尽也不会关闭路线选择或其他业务工具。旧检查点中的task_type额外字段会被忽略，既有计数保留，缺少计数时按默认值读取。工具总预算仍在每次执行前检查；公共检索/追问额度耗尽后仍可交付已有结果。全局Token、时间和模型预算耗尽仍按原规则终止，尚未实现最终回答预留预算。

法规检索的参数校验错误使用单独的纠错计数，仍计入全局工具预算。实际检索无候选也会占用检索次数。

API 和交互终端共用 `RunConfig.interactive()`：每次运行最多 56 步、16 次模型调用、24 次业务工具调用、240,000 个累计 token；工具纠错和检索仍各有上限。自动弹出的路线入口问题只记录一次，与最多三轮业务追问分开计数。模型可见本轮剩余预算。token 使用量按调用回执累计，单次响应可能使总量超过阈值。

交付时用 `finish.task_result.gaps` 按优先级列出缺口（label、kind、可选 question）。同一轮最多提出配置允许数量的 user_input 问题，一个 wait_id 逐题接收回答。material、verification、external 分别表示材料、核验和外部反馈，可由 Agent 单独发起材料请求。task.missing 保留短名，task.missing_kinds 标明分类；问句在 wait.questions 中。已答不代表已核实，模型在下一次提报时更新仍存在的缺口。旧 next_question 和未分类字符串仍可读取。未满足验收时为 partial（能力不足为 blocked），waiting_input 对应真实追问。

~~~powershell
.\.venv-rag\Scripts\python.exe -m pip install -r requirements-dev.txt
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

`knowledge_tools.py`负责法条结果转换。`tools/policy.py`定义ToolPolicy，阶段、副作用、超时和重试规则保存在原生工具的`metadata["civra"]`中；名称、说明和参数由`@tool`拥有。mock仍需显式选择，不能作为业务核验结论。

factory接受`case_service`，装配十项真实业务工具与法规检索，使用同一repository的RepositoryCasePort。传入已初始化的`retrieval_service`可复用检索资源。未传case_service时沿用内存演示工具；关闭mock_business_tools保留演示update_case。`build_tools(...)`及`setup.tools`返回`list[BaseTool]`，`AgentRunner(llm, tools, cases, store)`直接接收此列表。原`build_registry`、`register_*_tools`和`setup.registry`入口已移除；改用`create_case_tools(service)`、`create_knowledge_tools(...)`和`create_mock_tools(cases, ...)`。每次drive/resume退出时释放执行池，无需调用registry.close。

十项案件业务工具在 `backend/tools/case_tools.py` 中定义。案件阶段限制由工具与业务客户端共用，业务契约导出独立于 Agent 注册。真实案件服务另提供一个只读工具 `find_form_template`，咨询和办理均可调用。它按文书类型、案由关键词和可选领域查询官方空白原表，每页八项；候选提供原件地址和本地下载路径。仅支持生成 CivRA 草稿的候选返回 `draft_document_args`，其中的 ID 和版本可交给既有的 `draft_document`。原表的 `filling_supported` 描述业务层填写预览能力；下载空白表不会登记已填写成果。

十个案件工具共用一个绑定业务服务的执行适配，统一转换返回字典；运行策略在函数末尾集中声明，只读与核验反馈设置归工具层，案件阶段限制从业务层读取。RAG与演示工具直接在原生函数内调用业务并返回结果，控制工具保持原有JSON Schema约束的紧凑声明。`ToolPolicy`只保存配置，自动超时与重试仍只由运行时的`ToolExecutor`执行。

middleware在每次调用前读取案件，创建独立的`ToolContext`，通过`ToolRuntime[ToolContext].context`传给原生工具。工具签名的runtime参数不暴露给模型，也不共享可被后续步骤改写的上下文。案件工具共用`_make_invocation`映射业务请求、`_execute_case_tool`处理业务回执、`to_case_tool_result`转换引用，最终返回`ToolResult.model_dump(...)`字典，由LangChain序列化成ToolMessage。RAG直接使用runtime.context.deadline。`CaseToolClient`保留业务请求、阶段、版本校验及案件活动记录，使固定流程调用也遵守业务约束；Agent轨迹和执行限制由middleware负责。工具结果仍使用现有结构，未改法律业务规则。

普通工具和内置工具共用参数、阶段校验。有副作用的工具不自动重试；只读工具仅在可重试错误且deadline仍有余量时重试。Agent为一次逻辑工具调用分配timeout_s总时限，所有尝试共用；检索会将deadline传递给embedding HTTP请求。超时不代表工作线程已被强制终止。

检索资源加载、一致性校验、manifest和retriever创建位于`knowledge/retrieval/resources.py`；RetrievalService负责查询路由，保留原有索引注入和from_config入口。

## 顺序、等待与恢复

Runner使用同步invoke及max_concurrency=1，让框架按模型返回顺序执行同轮工具，无需手写条件锁和游标。遇ask_user或finish，middleware让后续工具返回未执行回执，不发生后续写入。此配置针对当前同步执行入口，不能直接套用于异步ainvoke。

ask_user结束当次框架调用并保存同一thread_id。resume_run先验证wait_id、案件版本和重复request_id，再向同一框架线程追加用户回复。框架负责恢复完整消息历史，middleware每次调用模型前重新读取案件状态。

~~~text
<工作区>/
├── cases.sqlite3           # 案件、会话、回执、agent_runs 及取消标记
└── runs/
    ├── agent.sqlite3       # 消息和任务，按会话隔离
    └── <run_id>/           # trace.jsonl、events.jsonl、results/step_NNNN.json
~~~

两个 SQLite 数据库需要一起保留。缺少框架检查点时拒绝重放工具。案件数据由 CaseStatePort 提供。使用 case_service 时，应用先在业务库登记用户输入，再调用 Agent。

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

成员三真实工具的create_agent装配已提供，接口与复现见`docs/case/submission-handoff.md`。真实生成/embedding效果、案例检索及最终答案的法律依据支持核验仍待联调。此前旧实现的DeepSeek试运行观察不能作为新实现的效果报告，也不能因迁移框架就宣称引用或法律结论得到改善。

办理交付约束：路线比较与清单属于过程；用户只要求比较或清单时，它们才构成本次交付。用户希望继续处理时，根据原始目标准备可用内容或调用 ask_user 获取关键输入。选路线不自动触发 get_route_workflow；结构化清单或进度计划明确需要时再调用。未知事实不得猜测，未执行的保存、核验和追问不得写成已完成。上述是模型行为指引，离线脚本测试只能验证流程支持，需通过真实模型验收判断遵循效果。

赔付调解方案的结束检查优先补问已付款和付款偏好。已答或已问过的信息和用户明确希望先看草案的情形跳过补问。待外部核实的事项可在条件化方案中说明，已用尽追问额度时保留未确认缺口。

办理任务保存在 LangGraph 的 tasks 通道（agent.sqlite3）。每项有稳定 ID、当前交付物、状态、缺口和材料／成果引用。新建运行沿用同一会话上一运行的任务，回复等待项继续原线程；/new 隔离案例。暂停任务可按 ID 接续，选择处理路线不自动更换交付物。运行 completed 只表示本次执行结束，任务仍可等待材料、部分交付或因能力缺失而受阻。/state 与 API 的 run.tasks 提供查看入口。旧图检查点没有 tasks 时按空列表读取。

入口模型同时判定任务变化。真实材料引用和文书文件由后端核对，语义判断（用户究竟要什么、分项范围、证据的证明力）仍需独立验收。指定办理入口跳过分类时先保留用户原话作为通用目标，可在控制工具中进一步明确。

提示管理：角色与公共规则、入口分类、两路由提示及运行限制模板集中在 model/prompts.py。分类和业务调用均通过 LangChain ChatPromptTemplate 渲染；原生消息历史由 MessagesPlaceholder 保留。routing.py 只处理分类数据和工具开放范围，runtime/middleware.py 检查限制并提供动态计数。公共提示不包含特定纠纷案例或固定追问示例；不同纠纷放在 tests/agent/test_prompts.py 和 scripts/agent/smoke_routes.py 中验证。运行清单的 prompt_sha256 覆盖全部集中提示文本。模板离线测试验证数据与消息保真，不等于真实模型语义验收。
