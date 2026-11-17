# CivRA 固定流程工作台 · Vue 版

由已确认的第二版 HTML 原型迁移，保留原来的布局、配色、七个功能页及固定流程。采用 Vue 3 单文件组件、Composition API、Pinia、Vue Router 和 Vite，代码语言是 JavaScript。

迁移源：`codex/guided-workflow-case-visuals` 的 `6429c91`。发布使用原分支 `codex/guided-workflow-case-visuals`，该分支的默认前端入口现为 Vue；本地迁移开发分支为 `codex/vue-guided-workflow`。原 HTML 仍在 `guided-workflow-prototype/`，可以逐页对照。此版本为本地交互原型，尚未接入后端接口。

## 启动

需要 Node.js 20.19+；本次使用 Node.js 22.21.1。

```powershell
# 在仓库根目录执行
cd frontend
npm ci
npm run dev
```

打开 <http://localhost:8782/>。开发服务器只监听本机，端口被占用时会明确报错，不会自动换端口。如果本轮预览还在运行，直接打开页面即可，不需要再启动一次。

```powershell
npm test
npm run build
npm run preview
```

`preview` 与 `dev` 使用相同端口，请停止其中一个再运行另一个。生产产物在 `dist/`。使用 History 路由，正式托管时需将 `/cases/*`、`/consult` 等前端路径回退至 `index.html`。

## 界面与交互

七个功能页顺序保持为：案情沟通 → 法律依据 → 维权路径 → 证据材料 → 结果分析 → 起诉状草稿 → 案情图谱。

五步办理过程保持为：收集案情 → 确认摘要 → 选择路径 → 办理事项 → 核对成果。

- 必答信息处理完之后，由用户确认摘要，才能选择处理路径。
- 法律查询和材料登记不受前述步骤限制；左侧“只咨询法律”可独立使用。
- 用户确认诉讼意向、身份为提出请求一方、尚未起诉且模板类型受支持，才开放起诉状工作台。
- 事实变化后，摘要与路径需要重新确认，历史草稿保留但不得直接导出；重新生成后才能导出。
- “暂不清楚”需要说明原因，未知还款不会被转换成零。
- 实际程序阶段与处理意向分开保存。选择调解、生成文书、记录反馈都不会自动推进程序或办结。
- 参考案例只放在结果分析原有三张卡片下方，以及案情图谱时间线下方的默认折叠区。其他页面不增加参考案例。

## 代码目录

```text
frontend/
├─ guided-workflow-prototype/   原 HTML 版，保留对照
├─ src/
│  ├─ App.vue                  整体外壳、文件选择、消息提示
│  ├─ router.js                案件与独立咨询路由
│  ├─ views/                   七个功能页、摘要确认与成果页
│  ├─ components/              侧栏、步骤条、案情卡、弹窗、案例区
│  ├─ stores/workspace.js      案件状态及提交动作
│  ├─ domain/policy.js         收集规则与功能进入条件
│  ├─ domain/seed.js           本地演示案件
│  ├─ domain/reference-*.js    模拟案例及筛选统计
│  ├─ composables/             页面跳转复用逻辑
│  ├─ services/                浏览器存储与 Markdown 下载
│  └─ assets/                  原第二版 CSS
├─ tests/                      流程、案例筛选、存储和状态动作测试
├─ package.json
├─ package-lock.json
└─ vite.config.js
```

每个页面由 Vue 模板、响应式数据和事件处理实现，没有使用 iframe 或 `v-html` 嵌入旧应用。两份 CSS 直接保留原文件内容。

## 本地记录兼容

继续使用存储键 `civra.guided-workflow.prototype.v2` 和原来的版本 2 数据结构。在**同一浏览器、同一来源地址**下，能够读取旧 HTML 的案件、材料记录、摘要确认、路径确认和草稿。

`http://localhost:8782` 与 `http://127.0.0.1:8782` 属于不同来源；端口或浏览器变化也不会自动共享记录。建议沿用 `http://localhost:8782/`。旧 HTML 若与 Vue 在同一来源打开，也会共用记录，请避免在两个页面同时编辑同一案件。

材料仅保存名称和大小，不保存文件正文。演示数据存储在浏览器，清理站点数据会清除记录，不是后端数据库。

## 后端对接边界

这次完成前端迁移，未修改或宣称接通 RAG、Agent、OCR、数据库与法院模板服务。

| 位置 | 当前行为 | 后续对接内容 |
| --- | --- | --- |
| `stores/workspace.js`、`services/` | 本地案件状态与用户确认 | 案件读写、服务器返回的收集规则、版本及确认校验 |
| `IntakeView.vue` | 固定字段追问，陈述保留待核对 | Agent 提取候选事实和追问，用户确认后写入案件 |
| `LawLookup.vue` | 一个明确标注的法律来源样例 | RAG 查询、来源编号、版本、页码、原文链接 |
| `EvidenceView.vue` | 材料名称、大小登记 | 上传、OCR、重复材料与冲突识别、补证清单 |
| `WorkflowView.vue` | 路径意向确认与本地反馈 | 适用渠道、材料、期限核验、办理反馈持久化 |
| `DraftView.vue` | 结构预览、表述确认、Markdown 导出 | 法院原表与模板版本、填充、Word/PDF 导出 |
| `OutcomesView.vue`、`ReferenceCases.vue` | 模拟结构与模拟案例 | 有依据的情形分析、真实类案及来源 |
| `VisualsView.vue` | 本地时间线、模拟样本统计 | 真实案件事件、材料关系、检索样本 |

前端成员负责 UI 与接口适配；成员三提供案件业务、证据、文书等能力；成员二提供检索与来源数据；成员四提供 Agent 调度和模型调用；成员五可据进入条件和确认流程补充联调验证。具体 API 名称与请求响应结构仍需按实际服务共同核对。

流程门槛目前在前端执行，正式接入时服务端也必须校验。界面不将本地模拟数据标为真实分析结果。

## 验证

见 `验收记录.md`，包含自动测试与浏览器验收范围。
