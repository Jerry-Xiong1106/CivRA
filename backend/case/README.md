# 案件业务层（成员三）

当前实现版本为 `case-tools-0.7.0`。最新安装、成员2/4/5接入、借贷试点和提交边界见 [提交交接说明](../../docs/case/submission-handoff.md)。下方四批记录保留开发历史，当前状态以最新说明为准。

第一批完成案件/事实/证据/诉求/输出模型、结构校验、Python 请求与返回契约、Runtime/Evaluation 适配、合成样例及测试。

第二批完成真实 `update_case`、`check_evidence`、`build_evidence_directory`，以及可替换 SQLite 存储、历史版本、并发版本检查、幂等重试、Runtime 工具注册和追问恢复演示。

第三批增加可切换 OCR、材料解析、明确公式计算、路线比较与待办、文书填写草稿、核验和展示数据。后续已加入普通财产案件受理费、Markdown/Word/PDF草稿导出、北京法院原件和三类原表DOCX回填。真实LLM仍由成员四注入，原表细项和原表PDF继续列为待办。

第四批提供统一 CaseToolClient，供 Agent 工具适配和固定流程调用，另有 40 个对接场景、10 份试点标注草案，以及两种调用模式的业务结果对比演示。

- [第一批设计与字段说明](../../docs/case/phase1-design.md)
- [第二批实现、运行与成员交接](../../docs/case/phase2-implementation.md)
- [第三批实现、CPU OCR 配置与调用示例](../../docs/case/phase3-implementation.md)
- [第四批安装、接入、场景和试点标注](../../docs/case/phase4-handoff.md)
- [公开请求与结果样例](../../data/examples/member3/phase2_calls/)

```powershell
python -m pip install -r requirements-case.txt
python scripts/case/demo_business.py
python -m unittest discover -s tests/case -v
```

```python
from backend.case import CaseService, SQLiteCaseRepository, load_development_case
from backend.case.contracts import CheckEvidenceRequest, InvocationContext

repository = SQLiteCaseRepository("var/cases.sqlite3")
# 初始化一次；已有案件不会被覆盖。
snapshot = repository.create_case(load_development_case())
service = CaseService(repository)
result = service.check_evidence(CheckEvidenceRequest(
    context=InvocationContext(request_id="check-1", case_id=snapshot.case_id,
                              experiment_run_id=None, deadline_at=None),
    case_version=snapshot.case_version, case_snapshot=snapshot,
))
print(result.status, result.summary)
```

工具接口在 `contracts.py`，请求/返回 JSON Schema 通过 `get_contract_manifest()` 实时生成。SQLite 是联调参考实现，可由成员一按同一协议替换。

与成员四代码组合后可运行 `python scripts/case/demo_agent_business.py`。演示使用真实业务工具和 ScriptedLLM；不代表真实模型表现。第三批演示为 `python scripts/case/demo_phase3.py --output-dir var/phase3-demo-new`，更多限制和待办见第三批说明。
