# Agent 执行层（成员4）

接口约定见 [docs/agent-runtime-contract.md](../../docs/agent-runtime-contract.md)。

## 文件

| 文件 | 内容 |
|---|---|
| `runner.py` | 主循环和 `start_run` / `resume_run` / `cancel_run` / `get_run` / `iter_events` / `get_trace` |
| `llm.py` | 统一模型接口：`OpenAICompatibleClient`（直接 HTTP）、`ScriptedLLM`（测试和演示） |
| `lc_model.py` | `LangChainLLMClient`：用 LangChain `ChatOpenAI` 调用模型，`build_runner` 默认使用 |
| `builtins.py` | Runtime 自己处理的动作：`ask_user`、`request_route_choice`、`finish` |
| `prompts.py` | 系统提示词；每次调用模型前拼入最新案件状态 |
| `store.py` | 执行记录、事件和检查点，存放在 `var/runs/<run_id>/` |
| `case_port.py` | Runtime 读取案件状态的接口，以及临时的内存实现 |
| `factory.py` | 一键组装可运行的 Agent：从 `.env` 读模型配置（默认 SiliconFlow），注册真实 `search_statutes` 和 mock 业务工具；`run_config_from_pilot` 读取评测计划的预算 |

相关文件：`backend/common/schemas.py`（公共数据结构）、`backend/tools/registry.py`（工具注册与执行）、`backend/tools/lc_tools.py`（把工具包装成 LangChain 工具）、`backend/tools/mock_tools.py`（mock 工具）。

LangChain 只用在模型和工具层；主循环、暂停恢复和检查点仍是本项目自己的实现。设置 `CIVRA_LLM_BACKEND=native` 可以换回直接 HTTP 的客户端。

## 运行

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests
.venv/bin/python scripts/demo_mock_run.py
```

测试和演示使用 mock 工具和 `ScriptedLLM`，不需要 API Key。

接入真实法条检索（模型仍是脚本，其他工具仍是 mock）：

```bash
.venv/bin/python scripts/rag/build_bm25_index.py
.venv/bin/python scripts/demo_mock_run.py --real-statutes
```

没有构建索引时，`tests/test_knowledge_tools.py` 中依赖真实索引的测试会跳过。

用真实模型对话（在根目录 `.env` 中填写 `SILICONFLOW_API_KEY=...`，该文件不会被提交）：

```bash
.venv/bin/python scripts/run_agent.py --message "我的车被追尾了，想要对方赔偿维修费"
.venv/bin/python scripts/run_agent.py --dry-run          # 没有 Key 时用脚本模型检查组装
.venv/bin/python scripts/run_agent.py --pilot-config evaluation/configs/pilot_v1.json   # 用评测计划的预算
```

Agent 追问时在终端里回答，回车跳过，`q` 取消。

## 当前状态

已完成：工具注册与参数校验、按阶段开放工具、超时与重试、主循环、追问暂停与恢复、路线选择、步数/工具调用/token/时间上限、检索—核验 3 轮上限、上下文压缩、最终结果过期标记、取消、执行记录、事件、检查点（换一个 Runner 实例也能继续）。

评测接口（成员5需求）：继续运行时校验 `wait_id` 和案件版本、`request_id` 防重复执行；分页导出执行记录并读取每步完整结果；运行配置清单（`get_runtime_manifest`）；提前结束时保留已完成部分。对应测试在 `tests/test_evaluation_interface.py`，名称对照见接口文档第 7.1 节。

尚未完成：
- 调试提示词（已用 DeepSeek `deepseek-chat` 跑通完整流程，见下方已知问题）
- 接入成员3的真实工具、成员2的 `search_cases`（`search_statutes` 已接入，见 `backend/tools/knowledge_tools.py`，默认关键词通道）
- 每份业务输出各自的过期标记（需要成员3的输出存储记录 `case_version`）
- 用模型生成摘要来压缩上下文（目前是规则压缩：缩减旧工具结果、省略最早几轮）

## 已知问题（DeepSeek 实测，2026-11-10）

- 一次完整运行约 3 万 token，超过评测计划中 Agent 的 24,000 上限，需要调整预算。
- 模型会说出检索结果里没有的具体规定（例如交强险财产损失限额），需要接入 `verify_output` 或补充语料。
- 模型曾把未确认的事实写进 `update_case`（注明了“待确认”）。
- 请求 `deepseek-chat` 时，DeepSeek 返回的实际模型名是 `deepseek-flash`，执行记录中记录的是实际模型名。
