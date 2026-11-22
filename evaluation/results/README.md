# 开发试点结果 / Development Pilot Results

**中文**

最新发布批次为 [20261121T085600Z-80f287](20261121T085600Z-80f287/README.md)，包含四模式对照、可共享的回答、逐项评分和缺陷复现记录。

本目录仅收录经确认可共享的开发试点证据，方便组员查看输入、回答、检索候选、评分规则和理由。它不是正式测试集，也不是最终法律准确性结论。

当前发布批次：[20261109T104304Z-dd61ec](20261109T104304Z-dd61ec/README.md)。包含5个开发任务、Direct LLM/RAG两种模式、10次真实生成。助手暂定必需项通过数量为3/5与4/5，标注和评分仍待团队复核。两种模式均未在交互设计题中接收控制器回复；Fixed Workflow与真实Agent效果尚未在本批比较。

其他运行批次默认被Git忽略，不能自动上传。隐藏事实池、完整受控标注、密钥、向量索引和本地环境不在本目录发布。请勿将本批已公开的任务及评分标准放入未见最终测试集。

查看顺序：先读批次README，再看`records.json`中的`output`和`retrieval.matches`，然后对照`scoring_checklist.json`、`review_scores.json`与`score_summary.json`。可以离线查看，无需密钥或API调用。

**English**

The [20261121T085600Z-80f287 batch](20261121T085600Z-80f287/README.md) contains the latest four-setting comparison, shareable answers, item reviews, and defect reproduction notes.

This directory publishes explicitly approved development-pilot evidence so team members can inspect inputs, answers, retrieval candidates, scoring rules, and reasons. It is not a final test set or a validated legal-accuracy claim.

Published batch: [20261109T104304Z-dd61ec](20261109T104304Z-dd61ec/README.md), with five development tasks, two settings, and ten real generations. Provisional required-item passes are 3/5 for Direct LLM and 4/5 for RAG. Annotations and assistant scores await team review. Neither baseline received controller replies on the interactive-design task; Fixed Workflow and real Agent effectiveness are not compared here.

Other batches stay Git-ignored by default. Hidden-fact pools, complete controlled annotations, credentials, vector indexes, and local environments are not published here. These disclosed tasks and scoring criteria must not become unseen final tests.

Start with the batch README, inspect `output` and `retrieval.matches` in `records.json`, then compare `scoring_checklist.json`, `review_scores.json`, and `score_summary.json`. No key or API call is needed to read the evidence.
