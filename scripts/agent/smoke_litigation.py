"""Bounded live litigation smoke using the official DeepSeek endpoint and synthetic materials."""
from datetime import datetime
from getpass import getpass
import os
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.agent.factory import build_runner
from backend.agent.model.llm import make_chat_model
from backend.agent.runtime.store import RunStore
from backend.case import CaseService, SQLiteCaseRepository
from backend.common.schemas import RunConfig, UserReply
from backend.knowledge.config import RetrievalConfig
from backend.knowledge.keyword.bm25_index import build_index
from backend.knowledge.retrieval.service import RetrievalService
from interactive import create_blank_case


def main():
    key = os.environ.get("DEEPSEEK_API_KEY") or getpass("DeepSeek API key (hidden): ").strip()
    if not key:
        raise SystemExit("No key provided")
    model = make_chat_model(model="deepseek-flash", base_url="https://api.deepseek.com", api_key=key,
                            timeout_s=45).model_copy(update={"extra_body": {"thinking": {"type": "disabled"}}})
    directory = ROOT / "var/litigation-smoke" / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6])
    directory.mkdir(parents=True)
    repo = SQLiteCaseRepository(directory / "cases.sqlite3")
    service = CaseService(repo)
    paths = [ROOT / "data/processed" / name for name in
             ("civil_code.jsonl", "civil_procedure_law.jsonl", "road_traffic_safety_law.jsonl")]
    retrieval = RetrievalService(RetrievalConfig(channel="keyword"), keyword_index=build_index(paths))
    runner = build_runner(llm=model, case_service=service, retrieval_service=retrieval, dotenv=None,
                          store=RunStore(directory / "runs"),
                          config=RunConfig(max_steps=32, max_model_calls=6, max_tool_calls=10,
                                           max_output_tokens=1400, max_total_tokens=100000)).runner
    case = create_blank_case(repo)
    conversation_id = "conv_" + uuid4().hex
    info = runner.enter_litigation(case.case_id, conversation_id=conversation_id)
    assert info.wait and info.wait.questions[0].field == "litigation_role"
    print("Records:", directory, flush=True)
    print("Intake:", info.wait.questions[0].text, flush=True)
    record = service.assets.put_file(case_id=case.case_id, file_id="file_" + uuid4().hex,
        file_version="1", file_name="维修明细.txt", media_type="text/plain", document_type="material",
        content="测试材料：维修人工费300元、零件费500元，合计800元。".encode())
    info = runner.resume_run(info.run_id, UserReply(answers={
        "litigation_intake": "我是原告。对方撞坏我的电动车，我想起诉要回维修费。请先核对我上传的明细，告诉我下一步需要什么。"
    }, file_ids=[record.file_id]), wait_id=info.wait.wait_id, conversation_id=conversation_id)
    print("Run:", info.status, "model calls:", info.usage.model_calls, flush=True)
    if info.wait:
        print("Next question:", [q.text for q in info.wait.questions], flush=True)
    if info.final:
        print("Answer:", info.final.answer, flush=True)
    trace = runner.get_trace(info.run_id)
    assert any(step.tool_name == "parse_documents" and step.status in ("ok", "insufficient") for step in trace)
    assert info.status in ("waiting_user", "completed"), info.error or info.status
    assert runner.litigation_progress(case.case_id, info)["steps"][-1]["status"] == "pending"


if __name__ == "__main__":
    main()
