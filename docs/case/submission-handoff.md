# 成员三业务代码提交与试点接入

实现版本：`case-tools-0.7.0`，业务Schema保留`0.1`。基于`develop@27c9a38`的LangChain `create_agent`实现准备。此交付包含成员三此前本地开发的业务层及本轮试点修复，可供成员四与五调用Python接口。真实模型、最终用户界面和独立法律评审仍待接入。

## 安装和检查

Python 3.12：

```powershell
python -m venv .venv-case
.\.venv-case\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv-case\Scripts\python.exe -m unittest discover -s tests/case -v
.\.venv-case\Scripts\python.exe scripts/case/check_handoff.py
.\.venv-case\Scripts\python.exe scripts/case/demo_shared_tools.py
.\.venv-case\Scripts\python.exe scripts/case/review_pilot_citations.py
```

上述检查使用临时合成案件、本地法条和脚本模型，不需要API密钥，不执行真实OCR或模型请求。OCR按`requirements-ocr.txt`另行安装。真实检索按成员二的环境和索引说明准备，不能用本模块的原文读取器代替检索算法。

## 成员二与四 来源读取和工具注册

```python
from backend.case import CaseService, SQLiteCaseRepository
from backend.case.research.knowledge_bridge import CorpusSourceResolver, executable_research_plan
from backend.agent.factory import build_runner

repository = SQLiteCaseRepository("var/cases.sqlite3")  # 应用另行创建/读取真实案件
sources = CorpusSourceResolver.from_directory()
service = CaseService(repository, source_resolver=sources)
setup = build_runner(case_service=service)  # 模型读取团队环境配置，真实检索需要准备索引
# 也可传入 retrieval_service=已初始化的成员二RetrievalService
# 应用创建案件后，调用 setup.runner.start_run(case_id, user_message)
# 退出时调用 setup.registry.close()
```

`CorpusSourceResolver`只读成员二已发布的三份JSONL，启动后复用已固定的内存快照。`SourcePointer.source_id`保持原始`provision_id`，`source_version`与最新RAG一致，采用`publication_date`，缺失时使用正文`sha256:`。生效日期单独保存在`effective_from`。此前案件中的生效日期版本指针通过唯一的精确别名读取同一原文；别名存在歧义时明确失败。Runtime输出使用该原文的规范版本。`manifest()`额外返回快照哈希。未知ID或版本明确失败，不降级到最新版。完整正文、PDF文件名、起止页、链接及登记时点均保留；不把下载时间当作法律效力核验时间。

成员二需复核此版本约定并提供后续权威版本管理。部分URL为官方数据库首页，当前可依据随库PDF页码核对；公开PDF下载地址由应用层提供。

`service.prepare_research()`仍返回业务查询计划。调用`executable_research_plan(plan)`得到当前可执行的`query/top_k`参数，尚不支持的`search_cases`进入`deferred`，日期/地区/案由要求保留在`unapplied_filters`，没有伪称已执行过滤。传入`expected_snapshot`可阻止错误知识库版本的请求。不要直接把整个ResearchQuery传给检索工具。

`register_case_tools`注册原十项业务工具，并默认使用`service.get_source`转换引用。Runtime整数`step_id`转为业务日志字符串，保留`run_id`关联。实验编号仍由评测直接调用时传入InvocationContext；共享Runtime关联由成员四对接。`build_runner(case_service=service, retrieval_service=...)`从空注册表装配真实检索及十项业务工具，使用同一repository的RepositoryCasePort，并替换演示update_case。关闭真实检索时可用`real_statutes=False`完成离线工具联调。

## create_agent返回结构与用户确认

Runtime工具返回的来源放在`data.matches[].source_ref`，相应原文放在`match.text`。来源版本字段是`source_version`，`locator`包含article、PDF页码、paragraph或chunk_id。业务结果仍保留在`data.data`及原有output_refs。业务信封中的source_refs保留原始版本指针，完整来源元数据与原文通过matches传递。

通过CaseService构建的Runner会在模型读取输入前保存用户消息，追问回复会追加到相同案件，再恢复相同LangGraph线程。保存消息本身不会确认任何事实；确认仍需引用已保存的message_id。应用已提前保存消息时，在`start_run`或`resume_run`传入该`message_id`避免重复写入，并传入正确的`expected_case_version`。`actor_id`由可信应用传入；CLI默认使用user。直接构造AgentRunner的调用方可显式选择`record_user_messages=True`。

## 成员五 借贷本金余额

`calculate_costs`新增`calculation_type="principal_balance"`、`rule_version="confirmed_principal_v1"`，原工具Schema无需更名。输入只能引用本案事实ID：

```python
arguments = {
    "calculation_type": "principal_balance",
    "rule_version": "confirmed_principal_v1",
    "inputs": {
        "principal_fact_id": "fact-loan",
        "has_repaid_fact_id": "fact-repayment-state",
        "repaid_principal_fact_id": "fact-repaid-principal",
    },
}
```

事实字段分别为`loan_principal`（Money）、`has_repaid`（boolean）、`repaid_principal`（Money）。输入是已确认、当前有效且无冲突的事实。`has_repaid=False`明确表示未还款，可省略第三项；缺失或未确认不能当作False。已还款时，必须明确本笔借款中用于归还本金的累计金额；不直接把所有转账相加。超过本金、币种不一致、存在冲突时返回`insufficient`，不输出余额。仅处理单笔借款本金，不处理利息或法定抵充顺序。

`lending`、`loan`作为业务规则的`private_lending`别名；新建案件建议先用`normalize_dispute_type()`统一名称。借贷证据检查新增本金、是否还款及条件性的已还本金问题，均为开发提示，不是法院强制材料要求。

## 成员四与五 答案引用检查

`CaseService.review_answer(request)`是无写入的Python辅助入口，Schema在`get_contract_manifest()`中。应用传入可信案件版本、原始回答和实际检索返回的来源；不得把gold或隐藏事实池注入本接口。

```python
report = service.review_answer({
    "context": context,
    "case_version": case_version,
    "answer": answer,
    "retrieved_sources": [{"source_id": actual_source_id, "source_version": actual_version}],
    "citations": [{
        "start": 0, "end": len(answer),
        "source_ref": {"source_id": actual_source_id, "source_version": actual_version},
        # 可选source_quote用于检查声明的逐字引用；不要将摘要当作引文。
    }],
    "required_citation_spans": [{"start": 0, "end": len(answer)}],
    "requires_citations": True,
})
```

上例仅演示结构。实际应逐条绑定主张，不应笼统把整份回答绑到一个来源。区间是Python Unicode字符下标，左闭右开，前端UTF-16下标需转换。可由界面渲染来源卡片，不强制既有草稿全部改成方括号格式；出现明确的全文来源ID标记时会核对其真实性及绑定关系。

检查未知来源、版本错误、无法定位原文、引文不符、越界/重复位置、缺少绑定及指定主张的引用覆盖。报告的`citation_audit`区分结构检查和语义支持，覆盖率仅针对调用方明确标出的主张。没有自动证明已找全所有法律主张。候选来源列表不等于全部已被引用。

真实语义检查注入`MeteredLLMReviewGateway(call_model)`到CaseService的`semantic_gateway`；生成/核验的调用预算与费用由成员四计量。未配置模型时报告明确保留`semantic_*`未执行项。Runtime的`finish`仍需成员四接此检查及修正/退出流程，本提交不自动更改其运行逻辑。

`review_pilot_citations.py`只审查已公开的5份RAG原始输出，按明确完整ID建立绑定，不从自然语言条号猜补ID，不修改原回答或评分。可用`--output var/pilot-citation-review.json`保存独立审查记录，已有文件不会覆盖。

## 测试修复与提交内容

- 共用演示比较保留实际事实、金额、公式、内容哈希等业务信息，只排除隔离案件衍生的身份哈希、输出ID和文件名。
- 来源适配故障测试先提供可通过草稿核验的来源，再让Runtime转换读取器失败，继续验证已保存输出可追回和内部错误不泄露。
- 原十个工具与新辅助入口均保留结构化失败、缺失信息与版本边界。
- 官方模板库含113份空白原表与113份实例；三类原表支持确认后追加文字，不代表全部细项、勾选项或原表PDF已完成。
- 不包含密钥、模型缓存、索引、个人案件数据库或运行输出。公开开发样例和官方原件作为项目资源随代码交付。

## 团队仍需完成

成员2复核来源完整性与实际检索配置；成员4复核新增case_service装配及消息持久化钩子，完成真实模型、答案核验/修正循环和运行预算联调；成员5接业务接口执行Fixed Workflow与Agent试点，并独立复核法律结果；成员1接用户确认、权限和页面。13项其他扩展继续按原台账，不计入本轮完成范围。
