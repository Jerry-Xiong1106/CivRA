# 前端 API 接入

前端在 `frontend/`（Vue 3）。HTTP 入口是 `backend/api/app.py`，协调逻辑在 `conversations.py`，HTTP 类型在 `schemas.py`。复用现有 AgentRunner、CaseService、SQLite 存储与 LangGraph 检查点，不另写案件处理循环。

## 启动

在仓库根目录安装依赖：

```powershell
.\.venv-rag\Scripts\python.exe -m pip install -r requirements-dev.txt
```

在服务端配置访问令牌与模型密钥。可以在当前终端隐藏输入：

```powershell
$env:CIVRA_API_TOKEN = [System.Net.NetworkCredential]::new('', (Read-Host 'API access token' -AsSecureString)).Password
$env:DEEPSEEK_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'DeepSeek API key' -AsSecureString)).Password
.\.venv-rag\Scripts\python.exe -m uvicorn backend.api.app:app --host 127.0.0.1 --port 8000 --workers 1
```

DEEPSEEK_API_KEY 使用与手动终端一致的 DeepSeek Flash 非思考配置；也可通过现有 CIVRA_LLM_* 服务端配置选择模型。不向前端传生成模型或 embedding 密钥。实际问答会产生 API 调用。

API 启动时初始化一次模型、真实案件服务、检索服务和来源读取器。需按 RAG 说明准备索引；缺失配置或索引会明确失败，不自动替换为演示工具。

- OpenAPI / Swagger：`http://127.0.0.1:8000/docs`
- OpenAPI JSON：`http://127.0.0.1:8000/openapi.json`
- 数据目录：默认 `var/api/`，可设置 `CIVRA_API_DATA_DIR`。
- CORS 默认允许 `http://localhost:5173`、`http://localhost:3000`；用 `CIVRA_API_CORS_ORIGINS` 配置逗号分隔的明确来源。
- 所有业务接口要求 `Authorization: Bearer <CIVRA_API_TOKEN>`。此版是单个可信工作区的访问令牌，不是多用户登录与权限系统。

## 接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/conversations` | 创建空白会话和真实案件 |
| GET | `/api/conversations/{id}` | 恢复页面：消息、当前运行、案件、材料、成果 |
| POST | `/api/conversations/{id}/messages` | 提交新问题，或回复追问／步骤确认 |
| POST | `/api/conversations/{id}/route` | 用户在界面点击路线入口：协商、调解或诉讼 |
| GET | `/api/conversations/{id}/events?run_id=...` | SSE 运行事件 |
| POST | `/api/conversations/{id}/files` | multipart 上传材料 |
| GET | `/api/conversations/{id}/artifacts/{artifact_id}` | 下载生成文件 |
| GET | `/api/form-templates/{template_id}/{version}` | 按目录版本下载官方空白 Word（使用相同的 Bearer 认证） |

### 创建和发送消息

`POST /api/conversations`：

```json
{"title": "交通事故处理"}
```

返回 201，取出 `conversation_id`。前端无需创建案件快照或设置案件阶段。

`POST /api/conversations/{id}/messages`：

```json
{"request_id": "client-generated-uuid", "text": "我遇到纠纷，需要帮助处理"}
```

返回 202 和 `request_id`、`run_id`、提交状态，可能已包含运行信息。202 表示已接收，不代表办理完成。执行在后台进行，前端用 SSE 或 GET 会话读取结果。

每次新提交使用新的 request_id；网络重试必须复用同一 ID 和内容。同 ID 同内容返回已有回执，不重复执行模型和工具；同 ID 不同内容返回 409。

### 路线入口、步骤确认和追问

**流程**：了解情况（还没选路线）→ 用户点击路线入口 → Agent 按该路线的步骤逐步引导；每一步交付后请用户确认，确认后自动进入下一步。

| 路线 | 步骤（step_id） |
| --- | --- |
| negotiation 协商解决 | prepare 核对诉求和材料 → communicate 提出方案并记录回复 → perform 核对约定和履行 |
| mediation 调解解决 | channel 核实调解渠道 → participate 提交材料并参加调解 → result 核对结果和履行 |
| litigation 诉讼准备 | jurisdiction 核实管辖和当事人 → evidence 证据目录 → draft 起诉状 → submit 提交 |

**选路线**（前端三个入口按钮）：

```json
POST /api/conversations/{id}/route
{"request_id": "client-uuid", "route": "litigation"}
```

返回 202 回执，后端保存路线并启动一轮运行，Agent 直接开始引导当前步骤。随时可以点另一个入口切换；正在等待的追问会被取消，每条路线的进度分别保留，切回来从上次的步骤继续。

**等待类型**（`run.wait.kind`），收到 `run.waiting_user` 后按类型展示：

| kind | 展示 | 回复 `reply` |
| --- | --- | --- |
| `clarification` | 逐个显示 `questions[]` | `{"answers": {"question_id": "回答"}}` 或 `{"text": "..."}` |
| `material_request` | 同上，并提示上传 | 同上，可在消息里附 `attachments` |
| `step_confirmation` | 显示 `message`（本步交付物正文）和 `summary`，给两个按钮 | `{"step_decision": "done"}` 或 `{"step_decision": "revise", "text": "要改什么"}` |

`wait.message` 是 Agent 随这次等待说的话（步骤确认时就是本步的完整交付物）。等待期间没有 `final`，请用它展示回答。

```json
{
  "request_id": "another-uuid",
  "wait_id": "服务端返回的wait_id",
  "reply": {"step_decision": "done"}
}
```

选"完成"后，后端把这一步记为已完成（记录引用用户这次的确认），Agent 在同一轮里接着引导下一步。选"还要修改"时步骤保持未完成，Agent 按用户意见修改后再请求确认。步骤确认只能用 `step_decision` 回复，其他等待不接受该字段（409）。

**进度**：GET 会话返回 `route_progress`（未选路线时为 null）：

```json
{
  "route": "litigation", "current_step": "evidence", "progress_version": 1,
  "steps": [
    {"step_id": "jurisdiction", "title": "核实管辖、当事人及具体程序要求", "status": "completed",
     "materials": ["当事人信息", "争议事实和诉求"], "completion_condition": "依据官方来源确认具体要求",
     "deliverable": "核对原告、被告、诉求和管辖法院，缺什么问什么", "tools": ["update_case", "search_statutes"],
     "note": "核实管辖、当事人及具体程序要求：确认：这一步已完成。", "output_refs": []}
  ]
}
```

step 的 status 为 pending、in_progress、completed、blocked 或 not_applicable；诉讼的 evidence、draft 步骤在 `output_refs` 列出已生成的证据目录和起诉状。

`expected_case_version` 可选，用于拒绝基于旧案件版本的提交。服务器检查当前等待项和问题 ID；不接受前端传工具名称、案件阶段或模型参数。等待中不能把普通新消息当作另一轮并行启动。

### SSE

对消息回执中的 run_id 建立连接。事件名包括：

- `run.started`、`run.resumed`
- `step.started`、`step.finished`：模型与工具的进度、状态和耗时
- `run.waiting_user`：追问或步骤确认（`data.wait` 同 GET 中的 `run.wait`）
- `run.completed`、`run.failed`、`run.cancelled`、`run.budget_exhausted`：本次运行的结束状态
- `state`：当前提交已处理后的运行快照，然后关闭连接

等待用户时连接也会正常结束；提交回复后重新连接同一 run_id，继续使用上次游标。新的用户问题会创建新 run_id，游标从零开始。

事件 ID 格式为 `run_id:seq`，支持 `Last-Event-ID` 重连，或 `after_seq` 查询参数。断线不取消运行，不会重新调用模型。流中空闲时发送 heartbeat 注释。

浏览器原生 EventSource 不能直接设置 Authorization 请求头；此版建议用 fetch 读取 SSE，并实现重连，不把访问令牌放在 URL 中。最终回复在运行结果中提供，**当前不逐 Token 推送正文**。

### 上传与下载

上传使用 multipart 的 `file` 字段。支持 PDF、纯文本、PNG/JPEG/WebP、DOCX，最大 20 MiB。可通过 `document_type` 查询参数登记材料类别；类别只表示调用方登记，不证明材料真实或有效。

返回 file_id、file_version 等文件元数据。后续消息携带引用：

```json
{
  "request_id": "upload-followup-uuid",
  "text": "请检查这份材料",
  "attachments": [{"file_id": "file_...", "file_version": "1"}]
}
```

上传只保存原文件，不自动 OCR、不自动确认事实或生成证据条目。提交引用后，模型根据任务调用材料解析等工具。文件引用会由后端校验属于当前案件，并补入模型输入。

GET 会话中的 outputs 含成果及导出文件引用。用其中的 artifact_id 下载；下载接口始终按当前会话案件查找，不能引用别的案件文件。材料与成果目录最多返回500项，超过时 inventory_truncated=true。

## 连续对话与持久化

手动终端：`python scripts/agent/interactive.py`，`/route litigation` 选路线，`/state` 查看进度，
`/upload "D:\材料\认定书.pdf"` 上传文件，`/new` 新开独立案件。

- 一段会话关联一个案件；一条新问题或一次选路线启动一次新运行。
- 新运行承接前一运行的原生消息历史，提示仍按当前案件状态和上下文长度限制组装。
- 追问和步骤确认的回复恢复同一运行。
- 每次提交保存独立结果快照；旧消息不会在后续恢复后全部变成重复的最终回复。
- cases.sqlite3 的 api_conversations、api_messages、agent_runs 分别记录会话、提交回执、每轮运行状态与图检查点 ID。
- runs/agent.sqlite3 按 conversation_id 保存消息历史。历史 run_id 读取当轮的图检查点。runs/<run_id>/ 保留 SSE、轨迹及工具结果。路线进度保存在案件库中。
- 正常等待中的运行可在服务重启后继续。执行途中进程退出会标记失败，不自动重放可能已写入的业务操作，历史结果保留。

## 错误与运行范围

- 401：访问令牌缺失或不正确。
- 404：会话、材料、运行或成果不存在／不属于该会话。
- 409：已有执行、过期等待、案件版本冲突或 request_id 内容冲突。
- 413 / 415：文件为空或超限／媒体类型不支持。
- 422：请求结构不合法。
- 503：执行队列已满或服务正在关闭；未接受的请求可稍后复用 request_id 重试。

此版仅支持一个服务进程、一个可信工作区；数据库锁会拒绝第二个协调器。不同会话的模型工作目前也串行执行，最多排队16次；同一会话不能并行提交。正式多用户部署仍需登录、用户级资源授权和持久化任务队列，不能把共享令牌当成用户身份系统。

文书和语义核验内部的额外模型调用沿用现有业务网关，其用量尚未合并进主 Agent 的总预算；本次没有改变已有计费范围。HTTP 接入不会自动修复模型的事实判断、法律解释或文书支持范围。

## 验证

```powershell
.\.venv-rag\Scripts\python.exe -B -m pytest tests/api -q -p no:cacheprovider
.\.venv-rag\Scripts\python.exe -B -m pytest tests -q -p no:cacheprovider
```

接口测试使用真实案件存储、LangGraph 检查点、文书导出与 FastAPI 请求处理，但模型为脚本模型，不需要密钥，不调用付费 API。覆盖重复提交、并发冲突、三条路线的入口与逐步确认、修改与切换路线、追问与重启恢复、跨轮历史、SSE 游标、上传范围和 Word/PDF 下载。
