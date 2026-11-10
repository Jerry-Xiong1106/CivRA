# 成员三提交前验证记录

日期：2026-11-10。基线：`develop@653ef33af9aaf319113432ac7589d9bc700d5e48`。提交分支：`feature/case-tools`。实现版本：`case-tools-0.6.0`。

## 已通过

| 检查 | 结果 | 范围 |
|---|---|---|
| `unittest discover -s tests/case` | 399项通过，无跳过 | 含25项新增来源/引用/借贷测试及此前18项Runtime联调检查 |
| 成员五评测测试与共享Runtime检查 | 28项通过 | `tests/evaluation`、`tests.test_agent_runtime`、`tests.test_evaluation_interface` |
| `scripts/case/check_handoff.py` | 40/40通过 | 十个工具各正常、非法、缺失、冲突场景 |
| `scripts/case/demo_shared_tools.py` | 通过 | Agent与固定序列各11次业务调用；业务输出和案件快照一致 |
| `scripts/case/review_pilot_citations.py` | 5/5发现缺少有效完整来源绑定 | 使用公开RAG原始回答，不重生成、不猜补ID、不改评分 |

测试使用本地语料、临时合成案件及ScriptedLLM，未调用付费模型，未将结构核对当作法律正确性证明。未运行全套RAG算法测试、真实OCR质量评测、真实LLM联调或前端验收。

## 环境与复现

Windows，Codex捆绑Python 3.12；业务依赖按`requirements-case-dev.txt`安装。附加评测检查需要团队检索依赖，本次在隔离目录使用项目声明的`langchain-core==1.7.4`。没有修改系统Python。

复现业务与交接检查的命令见[提交交接说明](submission-handoff.md)。附加共享检查可在装好团队依赖后运行：

```powershell
python -m unittest discover -s tests/evaluation -v
python -m unittest tests.test_agent_runtime tests.test_evaluation_interface -v
```

初次完整复核曾因C盘空间不足出现临时SQLite写入失败；改用E盘临时目录后399项完整重跑通过。两项原有联调测试已修正：隔离案件的身份字段不用于业务等价判断；来源故障测试确保真正进入目标适配失败分支。新增Runtime轨迹关联在整数step_id转字符串后通过测试。

## 结果解释

完整来源ID和可定位原文只能证明引用结构可核对。第004题这类适用条件扩大、第001题这类引用不支持推断的问题，仍需要成员四接真实语义核验，并由成员二/五独立复核。此次五题审查没有新增法律正确率或修改原试点评分。

正式实验仍应另备未见题、冻结规则，并分开报告引用ID有效性、指定主张覆盖、语义支持、多轮追问效果和运行成本。
