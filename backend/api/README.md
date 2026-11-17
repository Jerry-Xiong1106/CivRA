# 前端 API 接入

HTTP 层已实现，前端尚未实现。入口是 `backend/api/app.py`，协调逻辑在 `conversations.py`，HTTP 类型在 `schemas.py`。复用现有 AgentRunner、CaseService、SQLite 存储与 LangGraph 检查点，不另写案件处理循环。

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
| POST | `/api/conversations/{id}/messages` | 提交新问题、追问回复或路线选择 |
| GET | `/api/conversations/{id}/events?run_id=...` | SSE 运行事件 |
| POST | `/api/conversations/{id}/files` | multipart 上传材料 |
| GET | `/api/conversations/{id}/artifacts/{artifact_id}` | 下载生成文件 |

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

### 追问和路线选择

收到 `run.waiting_user`，根据 `wait.kind` 展示问题或菜单。问题可能有多个，逐个显示并按 question_id 收集回答。不要从普通回答文字里猜测菜单。

追问回复：

```json
{
  "request_id": "another-uuid",
  "wait_id": "服务端返回的wait_id",
  "reply": {"answers": {"服务端返回的question_id": "用户回答"}}
}
```

也允许 `reply: {"text": "用户自然语言回复"}`。路线菜单必须提交选项的 route 值：

```json
{
  "request_id": "route-choice-uuid",
  "wait_id": "服务端返回的wait_id",
  "reply": {"route_choice": "mediation"}
}
```

`expected_case_version` 可选，用于拒绝基于旧案件版本的提交。服务器检查当前等待项、问题 ID 和路线选项；不接受前端传工具名称、案件阶段或模型参数。等待中不能把普通新消息当作另一轮并行启动。

### SSE

对消息回执中的 run_id 建立连接。事件名包括：

- `run.started`、`run.resumed`
- `step.started`、`step.finished`：模型与工具的进度、状态和耗时
- `run.waiting_user`：问题或路线菜单
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

- 一段会话关联一个案件；一条新问题启动一次新运行。
- 新运行承接前一运行的原生消息历史，提示仍按当前案件状态和上下文长度限制组装。
- 追问回复恢复同一运行，不重新分类。
- 每次提交保存独立结果快照；旧消息不会在后续恢复后全部变成重复的最终回复。
- 会话和消息回执保存在 cases.sqlite3 的 api_conversations、api_messages 表；案件业务表不改变。
- Agent 消息、检查点和轨迹继续保存在 runs/，不在 HTTP 层复制另一套工具日志。
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

接口测试使用真实案件存储、LangGraph 检查点、文书导出与 FastAPI 请求处理，但模型为脚本模型，不需要密钥，不调用付费 API。覆盖重复提交、并发冲突、三路线选择、追问与重启恢复、跨轮历史、SSE 游标、上传范围和 Word/PDF 下载。
