# Agent 执行层（成员4）

接口约定见 [docs/agent-runtime-contract.md](../../docs/agent-runtime-contract.md)。

## 文件

| 文件 | 内容 |
|---|---|
| `runner.py` | 主循环和 `start_run` / `resume_run` / `cancel_run` / `get_run` / `iter_events` / `get_trace` |
| `llm.py` | 统一模型接口：`OpenAICompatibleClient`（真实模型）、`ScriptedLLM`（测试和演示） |
| `builtins.py` | Runtime 自己处理的动作：`ask_user`、`request_route_choice`、`finish` |
| `prompts.py` | 系统提示词；每次调用模型前拼入最新案件状态 |
| `store.py` | 执行记录、事件和检查点，存放在 `var/runs/<run_id>/` |
| `case_port.py` | Runtime 读取案件状态的接口，以及临时的内存实现 |

相关文件：`backend/common/schemas.py`（公共数据结构）、`backend/tools/registry.py`（工具注册与执行）、`backend/tools/mock_tools.py`（mock 工具）。

## 运行

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests
.venv/bin/python scripts/demo_mock_run.py
```

测试和演示使用 mock 工具和 `ScriptedLLM`，不需要 API Key。

## 当前状态

已完成：工具注册与参数校验、按阶段开放工具、超时与重试、主循环、追问暂停与恢复、路线选择、预算上限、取消、执行记录、事件、检查点（换一个 Runner 实例也能继续）。

尚未完成：
- 接入真实模型并调试提示词（`OpenAICompatibleClient` 已写好，未用真实 API 测试）
- 检索—核验循环 3 轮上限、token 预算
- 材料更新后把旧输出标记为 `stale`（需要成员3的输出存储）
- 接入成员2、3的真实工具
- 历史过长时的摘要
