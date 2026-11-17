# 测试入口

测试前安装开发依赖（含全部后端运行依赖）：

```powershell
.\.venv-rag\Scripts\python.exe -m pip install -r requirements-dev.txt
```

在仓库根目录执行全量离线回归：

```powershell
.\.venv-rag\Scripts\python.exe -B -m pytest tests -q -p no:cacheprovider
```

| 目录 | 验证范围 |
|---|---|
| `api/` | HTTP 鉴权、会话持久化、跨轮历史、追问与路线选择、SSE、上传及下载 |
| `agent/` | 两条主路由、公共对话限制、原生工具调用、等待恢复、预算、轨迹和模型接口 |
| `case/` | 案件数据、证据、计算、文书、核验、历史、存储及包依赖边界 |
| `rag/` | 检索结果、来源引用、索引和Agent检索接入 |
| `evaluation/` | 评测任务格式、基线、评分及索引一致性 |

`native_tools.py`仅用于构造测试工具和单独验证执行策略，不是生产注册表。
HTTP接口测试使用本地模拟服务；正式回归不调用付费生成模型。

## 人工及真实模型测试

只保留两个当前Agent测试入口：

```powershell
# 手动对话：空白案件，真实业务工具，输入DeepSeek密钥后自行提问
.\.venv-rag\Scripts\python.exe -B scripts/agent/interactive.py

# 两路由的四组真实模型验收，会调用付费生成模型
.\.venv-rag\Scripts\python.exe -B scripts/agent/smoke_routes.py
```

原生工具与业务场景的确定性演示仍在`scripts/case/`，其中部分被回归测试直接调用。
`scripts/agent/run_agent.py --dry-run --mock-statutes`保留为演示工具的离线CLI检查。

旧四路由、模型探测和一次性迁移脚本已删除。`var/`保留历史输出、数据库及验证记录，
这些结果不参与测试收集，也不会自动当成当前版本的验收结果。真实RAG使用本地索引，
未缓存的问题向量仍依赖项目已有的embedding配置。
