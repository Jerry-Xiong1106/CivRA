# CivRA 对话式案件工作区 · Vue

2026-11-18：保持现有“案情沟通 + 案情图谱”、侧栏、五个对话工作面板、模板来源和参考案例位置，按 GitHub `develop@922493e` 接入 HTTP 契约。本次接口适配保持已有 CSS 和布局，未修改后端业务代码。

## 两种记录

- **新建案件**：使用后端。第一次发送或上传时创建服务端会话，后续以服务端快照、追问、路线和成果为准。
- **已有本地记录 / 完整演示**：继续使用原有演示逻辑，保留已有内容，不自动把旧陈述、文件或模拟案例上传后端。要测试真实后端，请点击“新建案件”。
- **只咨询法律**：使用独立的后端会话，不与当前案件共用对话状态。现有后端仍会在内部创建案件记录；咨询/办理分类由 Agent 完成。

后端不可用时明确报错，不回退到模拟回答。旧浏览器存储键保留：`civra.guided-workflow.prototype.v2`。材料正文、成果、案件和对话保存在后端；本地保存会话索引和显示缓存。服务器还没有会话列表 API，清空浏览器存储后暂不能自动找回所有案件。

## 启动真实连接

1. 按 [后端 API 说明](../backend/api/README.md) 安装运行环境、准备 RAG 索引，配置模型密钥和 `CIVRA_API_TOKEN`，启动 `backend.api.app:app`，默认端口 8000。
2. 在 `frontend/.env.local` 配置（参考 [.env.example](.env.example)）：

   ```dotenv
   CIVRA_API_TARGET=http://127.0.0.1:8000
   CIVRA_API_TOKEN=与后端相同的工作区访问令牌
   ```

   令牌由 Vite 的 Node 服务代理注入，浏览器只请求同源 `/api`。不要使用 `VITE_*` 保存任何密钥。模型和 embedding 密钥只留在后端。`.env.local` 已加入忽略规则；修改后需重启 Vite。
3. 使用 Node.js 20.19+：

   ```powershell
   cd frontend
   npm ci
   npm run dev
   ```

   打开 <http://localhost:8782/>，点击“新建案件”开始。原有浏览器记录与来源绑定，建议继续用 localhost，不混用 127.0.0.1。

`npm run build` 生成 `dist/`；`npm run preview` 也配置同源代理。正式静态托管不包含 Vite 代理，需要部署对应的服务端鉴权/反向代理，并将前端 History 路由回退到 `index.html`。本地共享令牌方案沿用后端单可信工作区范围。

## 已适配的行为

- 创建/恢复会话、提交消息、最终回答、SSE 工具进度；后端当前不逐 token 推正文。
- `clarification`、`material_request` 使用真实 `wait_id + reply.text`；多问题可在同一消息回答。
- `route_selection` 只接受后端本轮提供的三个方向选项。没有菜单时，方向按钮发送评估意向，不能直接伪造已选路线。
- 前端确认、修改事实、生成草稿以用户消息提交，由后端验证和落库；不直接写入服务端 stage 或可信确认记录。
- 文件上传使用 multipart；成功后消息携带 `file_id/file_version` 再请求解析。上传不等于 OCR 成功。
- 最终来源保留 ID、版本、条款、PDF 页码和原文链接。当前 HTTP 不返回完整检索候选，页面不会伪造法条全文。
- 对已保存的路线、工作流、文书、图谱等成果按类型展示；下载使用真实 artifact_id。
- request_id 持久化与同内容重试、SSE 游标恢复、版本冲突刷新、案件切换隔离；失败不自动重复执行模型。
- 真案件中不显示模拟类案或模拟统计；缺少结构化检查明细不会显示成“无重复、无缺失”。

## 代码入口

| 文件 | 用途 |
| --- | --- |
| `src/services/civraApi.js` | 六类 HTTP 接口、SSE 解码、文件上传下载 |
| `src/services/backendBridge.js` | 单案请求协调、幂等重试、等待恢复、同步 |
| `src/domain/backendAdapter.js` | 后端快照、消息、来源、成果到现有界面的适配 |
| `src/stores/workspace.js` | 保留演示逻辑；新案件使用后端 bridge |
| `src/views/CaseConversation.vue` | 原对话布局、服务端回复/卡片、错误与进度 |
| `vite.config.js` | 仅本机服务的同源代理与服务端令牌 |

`domain/policy.js` 和 `domain/conversation.js` 中旧的固定门槛、关键词演示只用于旧记录/演示，不作为真实案件的后端规则。

## 验证与限制

```powershell
npm test
npm run build
```

本轮前端测试和构建通过。浏览器用明确标注“接口联调”的脚本 HTTP 服务验证创建、追问、路线、上传、草稿下载和刷新恢复；该服务没有真实模型、RAG 或 OCR，不能视为整套系统的真实效果验收。

可选浏览器联调（开发用途）：

```powershell
# 终端一
node tests/fixtures/contract-server.mjs
# 终端二
$env:CIVRA_API_TARGET = 'http://127.0.0.1:18992'
npm run dev -- --port 8892
```

此模式仅使用脚本回包。完成后停止这两个进程，并恢复真实后端地址；不要把测试服务当成业务服务。

完整功能缺口与成员对接见 [前后端对接与剩余开发清单](../docs/frontend/backend-alignment-20261118.md)。
