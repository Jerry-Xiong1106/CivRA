# 第四批工具接入样例

scenarios.json 提供十个工具各四类合成开发场景，共 40 个。seed 定义案件初始化条件，arguments 是传给业务工具的实际参数，expected 是独立编写、待成员五人工复核的状态、错误码和字段检查。call_version 可以显式设置过期调用版本，默认读取初始化后的版本。

pilot_annotations.json 提供 10 份关联场景的开发标注草案，状态为 pending_member5_review，供成员五复核并转换成其任务格式。可接受输出和禁止补造内容独立保存，实际业务调用只接收参数。

call_examples.json 保存本批实际运行的四十组完整请求与响应，并记录 scenarios.json 的 SHA-256。可按 tool 和 category 读取示例。expected 是独立开发预期，evaluation_response 是工具实际响应，两者分别记录。

```powershell
python scripts/case/check_handoff.py --output-dir var/phase4-handoff-new
```

脚本为每个场景创建独立案件与数据库，生成完整 ToolInvocation、成员五风格的响应、预期与检查结果。数据库保留供本地检查。get_contract_manifest 同时导出领域请求/返回 Schema 和 Runtime/固定流程共用的参数 Schema。

normal 表示格式和调用条件有效，业务结果可以为 insufficient。missing 表示材料、字段、确认或依据不足。conflict 包括材料日期差异、币种冲突、旧版本以及路线或确认状态不一致，解析或展示工具会按职责保留冲突候选。

Agent/固定流程演示使用同一套业务实现及脚本模型，执行命令见第四批交接说明。原始法院表格核对、真实模型联调和用户待定选型在交接说明中单独跟踪。
