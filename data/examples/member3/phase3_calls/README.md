# 第三批合成调用示例

十份同名业务 JSON 包含 request 和 result，来自真实 CaseService 调用；parse_documents 的样例使用真实本机 CPU PaddleOCR，不是固定识别返回。图像为生成的合成打印材料，不包含真实案件个人信息。

ocr_smoke_result.json 是该图像的一次真实识别记录；elapsed_ms 包含当前运行的初始化开销，不是速度基准。它不能说明手写识别正确率。

```powershell
python scripts/case/demo_phase3.py --output-dir var/phase3-demo-rebuild --ocr-image data/examples/member3/phase3_calls/materials/ocr-print-smoke.png
```

需先安装 requirements-case.txt 与 requirements-ocr.txt 并下载模型。新目录中生成数据库、十组调用结果、汇总和文书填写草稿。时间戳、文件名、OCR 分数及识别框可能随配置和运行变化，不应以文件字节相同作为通过条件。只有 text_segments 的 method 为 ocr 时才记为执行过 OCR。

这些是开发样例，不是独立评测集或金标准。请求依赖同一演示中的案件版本、已上传文件和先前用户确认；不要把单独一份请求直接交给空数据库。

法院原表未缓存、真实 LLM 未接入、Word/PDF 和受理费选型未完成。insufficient 及 checks_not_performed 必须保留，不能转换为所有核验通过。
