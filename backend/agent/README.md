# Agent 执行层

## 流程

```
了解情况（stage=consultation）
    │  Agent 回答问题、记录用户陈述的事实；遇到现实纠纷时比较三条路线并给出建议
    ▼
用户在界面点击路线入口 → runner.select_route()
    │  保存路线（stage=negotiation / mediation / litigation），Agent 立即开始引导当前步骤
    ▼
当前步骤 ──Agent 收集信息、调用工具、交付本步成果──▶ confirm_step（等待用户确认）
   ▲                                                        │
   │                   用户回复 step_decision="done"         │
   └──── 记录步骤完成（引用用户确认），同一轮里进入下一步 ◀──┘
                       用户回复 "revise"：按意见修改后再请求确认
```

- 每条路线的步骤来自 `backend/case/planning/routes.py` 的 `TEMPLATES`，每步要交付什么、常用哪些工具在
  `backend/case/planning/guide.py`（`RouteGuide`）。进度用业务层的 workflow progress 记录，必须引用用户真实说过的话；
  生成的材料本身不会让步骤完成。
- 切换路线时取消正在等待的追问；每条路线的进度分别保存，切回来从上次的步骤继续。
- 所有步骤完成后，Agent 总结成果，照常处理用户的新需求。

## 文件

| 文件 | 职责 |
|---|---|
| factory.py | 创建模型、工具列表、`RepositoryCasePort` 和 `AgentRunner` |
| runner.py | `start_run`、`select_route`、`resume_run`、`cancel_run` 和查询接口；create_agent 组装 |
| case_port.py | `CaseStatePort` 接口（快照、选路线、路线进度、记录用户陈述、完成步骤）和内存实现 |
| case_adapter.py | 基于 `CaseService` 的 `CaseStatePort` 实现 |
| model/llm.py | 模型构造、回复/用量序列化、`ScriptedLLM` 测试模型 |
| model/prompts.py | 系统提示、按阶段生成的引导（咨询／当前步骤／路线完成）、事实记录规则、上下文压缩 |
| runtime/middleware.py | 预算和取消、按阶段放行工具、执行业务工具、处理三个内置动作 |
| runtime/execution.py | 工具参数校验、超时和重试 |
| runtime/builtins.py | 内置动作 `ask_user`、`confirm_step`、`finish` |
| runtime/state.py | 运行元数据、预算判断和最终输出规则 |
| runtime/store.py | LangGraph 检查点、运行记录、步骤/事件日志和用量累计 |

## 工具放行

- 业务工具按案件阶段开放：`OPERATION_STAGES`（`backend/case/operations.py`）是唯一的阶段表，
  例如 `draft_document`、`build_evidence_directory` 只在诉讼阶段可用。
- `confirm_step` 只在三条路线阶段可用，且只能确认当前步骤；`ask_user` 受追问轮数限制。
- 同一个工具连续失败 `max_tool_failures`（默认 2）次后，本轮不再提供给模型，并写入未解决事项。
- 法条检索受 `max_search_calls`（默认 8）限制；全局还有步数、模型调用、工具调用、token 和时长预算。

## 等待与恢复

`ask_user` 和 `confirm_step` 会结束当次框架调用，保存在同一个 LangGraph 线程里。`RunInfo.wait` 给出
`kind`（clarification / material_request / step_confirmation）、问题、`message`（Agent 随这次等待说的话，
步骤确认时是本步交付物）。`resume_run` 校验 wait_id、案件版本和重复请求，再向同一线程追加用户回复。

```text
<工作区>/
├── cases.sqlite3           # 案件、路线进度、会话、回执、agent_runs
└── runs/
    ├── agent.sqlite3       # 消息历史，按会话隔离
    └── <run_id>/           # trace.jsonl、events.jsonl、results/step_NNNN.json
```

## 运行

```bash
.venv/bin/python -B -m pytest tests -q -p no:cacheprovider
.venv/bin/python scripts/agent/run_agent.py --dry-run --mock-statutes
.venv/bin/python scripts/agent/run_agent.py --message "我的车被追尾了" --route negotiation
.venv/bin/python scripts/agent/interactive.py      # 真实案件库，/route litigation 选路线
```

真实模型读取 `CIVRA_LLM_API_KEY`（或 `SILICONFLOW_API_KEY`），`CIVRA_LLM_MODEL`、`CIVRA_LLM_BASE_URL` 选择 OpenAI
兼容服务，`max_retries` 固定为 0。法条检索默认“精确条号＋向量”，需要 `data/index/vector_metadata.npz` 和
embedding 服务；`CIVRA_RETRIEVAL_MODE=keyword` 改用 BM25。

```python
from backend.agent.factory import build_runner

setup = build_runner(case_service=service)          # 真实业务工具和案件库
info = setup.runner.start_run(case_id, "我的车被追尾了")
info = setup.runner.select_route(case_id, "litigation", history_run_id=info.run_id)
info = setup.runner.resume_run(info.run_id, UserReply(step_decision="done"), wait_id=info.wait.wait_id)
```

## 尚待验证

离线测试只证明调用链和规则；Agent 是否按步骤给出有用的交付物、何时请求确认，需要用真实模型验收。
