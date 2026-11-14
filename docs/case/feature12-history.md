# 第12部分：案件保存、补充材料与继续处理

更新：2026-11-09。继续使用同一案件数据库，没有再创建一套独立业务数据库。案件、文件、提取文字、成果及历史版本沿用现有存储；本轮补充历史分页、版本比较和恢复前的影响检查。

## 三个Python入口

```python
case = service.repository.get_case(case_id)
base = {"context": {"request_id": "resume-001", "case_id": case_id,
                    "experiment_run_id": None, "deadline_at": None},
        "case_version": case.case_version}

history = service.get_case_history({**base, "limit": 50})
changes = service.compare_case_versions({**base, "from_version": 1,
                                        "to_version": case.case_version})
continuation = service.prepare_case_continuation({**base, "since_version": 1})
```

三个接口均只读，不把模型输出直接写成事实，不重写旧版本，不自动恢复Agent执行。

### 历史分页

`get_case_history`返回审计条目、固定上界`through_version`和下一页`next_after_version`。继续翻页时把上一页上界传回，避免新版本插入改变本次浏览范围。每页最多200条；请求仍须携带当前案件版本，旧版本请求返回`version_conflict`。

### 版本比较

`compare_case_versions`要求同案、从旧到新。对事实、证据、请求和用户陈述返回新增、移除、修改前后值；另列纠纷类型、管辖、阶段、所选路线和目标变化。时间戳或版本号变化不被单独当作实体事实变化。

这不提供“回滚覆盖”接口。读取旧版本用于比较，后续改正仍走有真实用户确认和版本校验的更新接口。

### 继续处理准备

`prepare_case_continuation`返回当前案件快照、输入文件版本、未关联到证据的文件、每份最新成果及影响提示。**每份成果按其自身原案件版本比较**，而不是统一从`since_version`比较，避免漏掉更早的过期成果。

- 输出直接关联的事实/证据/请求变化：列出具体依赖编号，提醒复核和重新生成。
- 新材料、新请求或其他案件前提变化：即使原输出没有直接引用，也保守提示可能影响结论。
- 新用户陈述：提示先解释、确认；不会自动认定某笔付款或责任比例已经成立。
- 新上传文件尚未关联证据：即使案件版本未增加，也提示先解析、确认。解析成功不等于已经纳入事实。
- 旧成果原文、`review_status`和版本保留。`needs_review`是本次恢复视图中的提示，不是静默修改历史数据。

`suggested_interfaces`只建议下一步接口。出现`CaseIntakeService.prepare_review`时，调用第1部分原有事实确认准备接口；真实保存仍走`update_case`。案件在读取期间更新则返回版本冲突，让调用方重新读取。

清单上限为500个文件版本、500份最新成果；超出返回`inventory_truncated=true`，不能当完整清单使用，实际应用需接成员一的分页查询。旧输出的具体历史版本仍用`assets.get_output(case_id, output_id, output_version)`读取。

## 推荐衔接

上传登记 → `parse_documents` → 展示候选文字/事实 → 记录真实用户回复 → `CaseIntakeService.prepare_review` / `update_case` → `prepare_case_continuation` → Runtime按影响清单选择重算、分析、改稿和核验。

“恢复案件数据”与“恢复Agent运行”是两个步骤。本模块不读取或创造模型检查点、不恢复旧预算、不创建run_id、不自动选择路线或重新开启已关闭案件。

## 对接与验证

| 成员 | 对接内容 |
| --- | --- |
| 1 | 用户案件权限、上传页面、历史/差异/过期成果页面、应用存储与大清单分页 |
| 4 | 运行检查点、新run_id、解释新陈述、选择哪些成果重跑及预算/暂停/恢复 |
| 5 | 重启后恢复、旧版本保持、材料新增而版本未变、并发版本冲突的独立场景 |

成员一替换本地存储时，补充`repository.list_history(case_id, after_version, through_version, limit)`和`assets.list_case_inventory(case_id, limit)`；后者应在一致读取中返回头版本、文件及各输出的最新版本和截断标记。所有接口在应用完成授权后调用；本地业务层不承担登录和用户权限管理。

新增`test_case_continuation.py`共10项测试。代码：`history.py`、`repository.py`、`assets.py`和CaseService包装。
