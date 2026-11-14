# 建议标题

feat(case): add business tools and integrate LangChain create_agent

# PR说明

协作分支此前只有案件模块占位文件，Fixed Workflow和Agent无法调用成员三的真实业务实现。本PR交付十项统一Python业务工具、版本化案件/证据模型、SQLite参考存储、材料解析、计算、路线/待办、文书与核验接口，并附测试及合成演示。

针对当前Direct LLM/RAG试点，新增同版本法条原文读取适配器、答案引用ID/位置/覆盖检查和单笔借贷本金余额规则。缺少还款信息时返回待确认事项，明确确认10000元本金及2000元已还本金时计算余额8000元。来源不存在、版本不符和引文无法定位时明确报告，不补造来源。

基于`develop@27c9a38`适配LangChain `create_agent`。通过`build_runner(case_service=service, retrieval_service=...)`装配十项真实业务工具与法规检索，使用同一案件repository，并在模型读取输入前保存用户消息。继续运行支持wait_id、案件版本校验和request_id去重，应用已保存消息可传message_id。

工具返回通过`data.matches[].source_ref`提供结构化来源，正文放在match.text。法规版本与最新RAG一致，采用发布日期或正文哈希；此前按生效日期保存的指针仅通过唯一精确别名解析。业务调用仍关联Runtime的run_id/step_id。更新联调脚本导入路径，并固定带哈希示例材料的LF换行格式。

包含北京法院官方模板库及三类确认后原表DOCX填写接口。原表按栏目组追加文字，细项/勾选框和原表PDF仍待完善；既有独立草稿支持Markdown/Word/PDF。

## 验证

- 完整测试520项通过，另有198个子测试通过，无跳过。
- 40/40个交接场景通过，十工具Agent与固定流程的业务输出和案件快照一致。
- 本地真实RAG调用、引用回读及10项新增create_agent回归通过。

具体测试结果及验证范围见[验证记录](submission-validation.md)。完整测试集、40个交接场景、十工具Agent/固定序列演示和公开RAG结果的离线引用检查均提供复现命令。新增回归覆盖真实create_agent与本地RAG、来源版本、旧指针、追问恢复、应用消息复用及初次保存失败后的重试。

## 对接边界

- 成员2复核来源版本兼容与实际索引配置，继续维护来源治理。
- 成员4复核factory装配和用户消息持久化钩子，完成真实模型、最终答案核验与修正循环联调。review_answer继续作为独立Python辅助入口。
- 成员5接真实业务工具运行Fixed Workflow和Agent；本PR未改动已有试点原始回答、gold或评分。
- 成员1接页面、权限及真实用户确认；本PR提供本地参考存储与接口。

完整入口和样例见[提交交接说明](submission-handoff.md)。
