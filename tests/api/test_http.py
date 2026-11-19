"""HTTP integration with real case storage and scripted models; no paid API calls."""
import json
import time
from threading import Event

import pytest
from fastapi.testclient import TestClient

from backend.agent.factory import build_runner
from backend.agent.model.llm import ScriptedLLM, tool_call
from backend.agent.runtime.store import RunStore
from backend.api.app import MAX_UPLOAD_BYTES, create_app
from backend.api.conversations import Conversations
from backend.case import CaseService, SQLiteCaseRepository
from backend.common.schemas import RunConfig, LLMResponse

TOKEN = "test-access-token"
HEADERS = {"Authorization": "Bearer " + TOKEN}


def components(directory, script, *, entry="case_work", **kwargs):
    directory.mkdir(parents=True, exist_ok=True)
    case_service = CaseService(SQLiteCaseRepository(directory / "cases.sqlite3"))
    setup = build_runner(
        llm=ScriptedLLM(script), case_service=case_service, real_statutes=False, dotenv=None,
        store=RunStore(directory / "runs"), config=RunConfig(conversation_route=entry),
    )
    return Conversations(setup.runner, case_service, **kwargs)


@pytest.fixture
def api(tmp_path):
    services = []

    def build(script, name="app", **kwargs):
        service = components(tmp_path / name, script, **kwargs)
        services.append(service)
        return service, TestClient(create_app(service, token=TOKEN), headers=HEADERS)
    return build


def create(client):
    response = client.post("/api/conversations", json={"title": "Test"})
    assert response.status_code == 201, response.text
    return response.json()["conversation_id"]


def send(client, conversation, **payload):
    response = client.post(f"/api/conversations/{conversation}/messages", json=payload)
    assert response.status_code == 202, response.text
    return response.json()


def wait(client, conversation):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        response = client.get(f"/api/conversations/{conversation}")
        assert response.status_code == 200, response.text
        if not response.json()["pending"]:
            return response.json()
        time.sleep(0.01)
    pytest.fail("execution did not finish")


def test_auth_payload_validation_and_documented_endpoints(api):
    service, client = api([tool_call("finish", answer="done")])
    with client:
        assert client.post("/api/conversations", headers={"Authorization": ""}, json={}).status_code == 401
        assert client.get("/api/conversations/missing").status_code == 404
        conversation = create(client)
        for body in (
            {"request_id": "a", "text": "hi", "case_id": "forged"},
            {"request_id": "a", "text": "hi", "stage": "litigation"},
            {"request_id": "a", "reply": {"text": "answer"}},
            {"request_id": "a", "text": "hi", "reply": {"text": "answer"}, "wait_id": "w"},
            {"request_id": "a", "text": " "},
        ):
            assert client.post(f"/api/conversations/{conversation}/messages", json=body).status_code == 422
        schema = client.get("/openapi.json").json()
        assert schema["paths"]["/api/conversations/{conversation_id}/messages"]["post"]["responses"]["202"]


def test_agent_finds_blank_defense_and_downloads_exact_catalog_version(api):
    import hashlib
    rows = []
    def pick(messages, tools):
        assert "find_form_template" in {t.name for t in tools}
        return tool_call("find_form_template", query="买卖合同", document_type="defense", domain="民事")
    def present(messages, tools):
        result = json.loads(messages[-1]["content"])
        rows.extend(result["data"]["candidates"])
        selected = next(r for r in rows if r["title"] == "买卖合同纠纷民事答辩状")
        assert selected["draft_document_args"] is None and not selected["filling_supported"]
        return tool_call("finish", answer="可下载空白原表：" + selected["blank_download_path"])
    service, client = api([pick, present], entry="consultation")
    with client:
        conversation = create(client)
        send(client, conversation, request_id="lookup", text="给我买卖合同纠纷的空白答辩状")
        run = wait(client, conversation)["run"]
        assert run["status"] == "completed"
        path = run["final"]["answer"].split("：", 1)[1]
        downloaded = client.get(path)
        record = next(r for r in service.service.official_forms.library.records()
                      if r["kind"] == "blank" and r["title"] == "买卖合同纠纷民事答辩状")
        assert downloaded.status_code == 200
        assert hashlib.sha256(downloaded.content).hexdigest() == record["sha256"]
        assert client.get(path + "_wrong").status_code == 404
        assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert not wait(client, conversation)["outputs"]


def test_intake_is_separate_from_three_followup_rounds(api):
    service, client = api([
        *(tool_call("ask_user", questions=[{"question_id": str(n), "text": f"请补充事项{n}。"}])
          for n in range(1, 5)),
        tool_call("finish", answer="已列明现有信息和未确定事项。", task_result={"missing": ["外部反馈"]}),
    ])
    with client:
        cid = create(client)
        assert client.post(f"/api/conversations/{cid}/routes/litigation/enter",
                           json={"request_id": "start"}).status_code == 202
        for count in range(4):
            run = wait(client, cid)["run"]
            assert run["status"] == "waiting_user"
            checkpoint = service.runner.store.load_checkpoint(run["run_id"])
            assert checkpoint.clarification_rounds == count
            send(client, cid, request_id=f"answer-{count}", wait_id=run["wait"]["wait_id"],
                 reply={"answers": {run["wait"]["questions"][0]["question_id"]: "暂不清楚"}})
        run = wait(client, cid)["run"]
        assert run["status"] == "completed" and run["wait"] is None
        assert service.runner.store.load_checkpoint(run["run_id"]).clarification_rounds == 3


def test_intake_is_separate_from_three_followup_rounds(api):
    service, client = api([
        *(tool_call("ask_user", questions=[{"question_id": str(n), "text": f"请补充事项{n}。"}])
          for n in range(1, 5)),
        tool_call("finish", answer="已列明现有信息和未确定事项。", task_result={"missing": ["外部反馈"]}),
    ])
    with client:
        cid = create(client)
        assert client.post(f"/api/conversations/{cid}/routes/litigation/enter",
                           json={"request_id": "start"}).status_code == 202
        for count in range(4):
            run = wait(client, cid)["run"]
            assert run["status"] == "waiting_user"
            checkpoint = service.runner.store.load_checkpoint(run["run_id"])
            assert checkpoint.clarification_rounds == count
            send(client, cid, request_id=f"answer-{count}", wait_id=run["wait"]["wait_id"],
                 reply={"answers": {run["wait"]["questions"][0]["question_id"]: "暂不清楚"}})
        run = wait(client, cid)["run"]
        assert run["status"] == "completed" and run["wait"] is None
        assert service.runner.store.load_checkpoint(run["run_id"]).clarification_rounds == 3


def test_litigation_entry_resumes_wait_and_tracks_real_feedback(api):
    from tests.agent.test_tasks import finish
    service, client = api([finish("先整理诉求和材料。", delivered_text="先整理诉求和材料。", missing=[])])
    with client:
        conversation = create(client)
        route = f"/api/conversations/{conversation}/routes/litigation"
        def enter(key):
            response = client.post(route + "/enter", json={"request_id": key})
            assert response.status_code == 202, response.text
            return response.json()
        first = enter("start")
        state = wait(client, conversation)
        checkpoint = service.runner.store.load_checkpoint(first["run_id"])
        assert checkpoint.intake_wait_id == state["run"]["wait"]["wait_id"]
        assert checkpoint.clarification_rounds == 0
        assert state["case"]["selected_route"] == "litigation"
        assert state["route_progress"]["current_step"] == "jurisdiction"
        assert state["run"]["status"] == "waiting_user" and not service.runner.llm.calls
        waiting = state["run"]["wait"]
        assert state["route_progress"]["wait_id"] == waiting["wait_id"]
        assert enter("start")["run_id"] == enter("return")["run_id"] == first["run_id"]
        upload = client.post(f"/api/conversations/{conversation}/files",
                             files={"file": ("bill.txt", b"Repair: 300", "text/plain")})
        ref = {k: upload.json()[k] for k in ("file_id", "file_version")}
        send(client, conversation, request_id="role", wait_id=waiting["wait_id"], attachments=[ref],
             reply={"answers": {"litigation_intake": "我是原告，希望追回维修费。"}})
        state = wait(client, conversation)
        assert state["route_progress"]["role"] == "plaintiff"
        assert state["route_progress"]["task_id"] == state["run"]["tasks"][0]["task_id"]
        assert state["route_progress"]["files"] == [ref["file_id"]]
        assert enter("back")["run_id"] == first["run_id"]
        assert client.post(route + "/feedback", json={
            "request_id": "fake-draft", "event": "draft_reviewed", "note": "已复核"
        }).status_code == 409
        for event, step, step_status in [
            ("intake_checked", "evidence", "pending"), ("submitted", "submit", "in_progress"),
            ("correct_evidence", "evidence", "in_progress"), ("accepted", None, "completed"),
        ]:
            service.runner.llm = ScriptedLLM([tool_call("finish", answer="已记录用户反馈。")])
            body = {"request_id": event, "event": event, "note": "这是我的实际办理反馈"}
            receipt = client.post(route + "/feedback", json=body)
            assert receipt.status_code == 202, receipt.text
            state = wait(client, conversation)
            assert state["run"]["status"] == "completed", state["messages"][-1]
            progress = state["route_progress"]
            assert progress["current_step"] == step
            row = next(s for s in progress["steps"] if s["step_id"] == (step or "submit"))
            assert row["status"] == step_status
            assert client.post(route + "/feedback", json=body).json()["run_id"] == receipt.json()["run_id"]
            assert wait(client, conversation)["route_progress"]["progress_version"] == progress["progress_version"]


def test_switching_to_litigation_preserves_unfinished_goal(api):
    service, client = api([tool_call("ask_user", questions=[{"question_id": "q", "text": "请补充"}])])
    with client:
        conversation = create(client)
        send(client, conversation, request_id="message", text="帮我准备协商材料")
        before = wait(client, conversation)["run"]
        endpoint = f"/api/conversations/{conversation}/routes/litigation/enter"
        assert client.post(endpoint, json={"request_id": "switch"}).status_code == 409
        service.runner.llm = ScriptedLLM([tool_call("ask_user", questions=[{"question_id": "role", "text": "你的角色？"}])])
        switched = client.post(endpoint, json={"request_id": "switch", "switch_wait_id": before["wait"]["wait_id"]})
        assert switched.status_code == 202
        after = wait(client, conversation)
        assert after["run"]["run_id"] != before["run_id"]
        assert after["run"]["tasks"][0]["task_id"] == before["tasks"][0]["task_id"]
        assert after["run"]["tasks"][0]["status"] == "paused"


def test_return_to_litigation_after_a_side_task_and_restart(api):
    from tests.agent.test_tasks import finish, route
    service, client = api([
        finish("诉求已记录，尚缺收费明细。", missing=["收费明细"], delivered_text="诉求已记录"),
        route("new", "text", "索要明细的微信"), finish("请发明细。", delivered_text="请发明细。", missing=[]),
    ], entry="auto")
    with client:
        conversation = create(client)
        endpoint = f"/api/conversations/{conversation}/routes/litigation/enter"
        client.post(endpoint, json={"request_id": "start"})
        info = wait(client, conversation)["run"]
        original_id = info["tasks"][0]["task_id"]
        send(client, conversation, request_id="intake", wait_id=info["wait"]["wait_id"],
             reply={"answers":{"litigation_intake":"我是原告，希望退还多收费用。"}})
        assert wait(client, conversation)["run"]["tasks"][0]["status"] == "partial"
        send(client, conversation, request_id="side", text="先帮我写微信索要明细")
        side = wait(client, conversation)["run"]
        assert [t["status"] for t in side["tasks"]] == ["paused", "delivered"]
        assert side["tasks"][1]["route_step"] is None
    reopened = Conversations(
        build_runner(llm=ScriptedLLM([tool_call("ask_user", questions=[{"question_id":"bill","text":"收到明细了吗？"}])]),
                     case_service=service.service, real_statutes=False, dotenv=None,
                     store=RunStore(service.runner.store.root)).runner, service.service)
    with TestClient(create_app(reopened, token=TOKEN), headers=HEADERS) as client:
        receipt = client.post(endpoint, json={"request_id": "return"})
        assert receipt.status_code == 202 and receipt.json()["run_id"] != side["run_id"]
        state = wait(client, conversation)
        assert state["route_progress"]["task_id"] == original_id
        assert len(state["run"]["tasks"]) == 2 and state["run"]["wait"]


def test_litigation_prepares_real_directory_and_draft_then_confirms_submission(api):
    from datetime import datetime, timezone
    from backend.case import load_development_case
    from backend.case.models import CaseSnapshot
    from backend.case.drafting.templates import CURRENT_VERSION
    from tests.agent.test_tasks import route
    service, client = api([], entry="auto")
    with client:
        conversation = create(client)
        endpoint = f"/api/conversations/{conversation}"
        case = client.get(endpoint).json()["case"]
        fixture = load_development_case().model_dump(mode="json")
        fixture.update(case_id=case["case_id"], case_version=2, created_at=case["created_at"],
                       updated_at=datetime.now(timezone.utc).isoformat())
        service.repository.replace_case(CaseSnapshot.model_validate(fixture), expected_case_version=1)
        uploaded = client.post(endpoint + "/files", files={"file": ("bill.txt", b"Repair cost 300", "text/plain")})
        assert uploaded.status_code == 201
        file_id = uploaded.json()["file_id"]
        def after_read(messages, tools):
            assert any(m["role"] == "tool" and file_id in m["content"] for m in messages)
            return tool_call("build_evidence_directory")
        service.runner.llm = ScriptedLLM([
            after_read,
            tool_call("finish", answer="已整理证据目录，请复核。"),
        ])
        assert client.post(endpoint + "/routes/litigation/enter", json={"request_id": "enter"}).status_code == 202
        state = wait(client, conversation)
        directory, = state["route_progress"]["artifacts"]["evidence"]
        task = state["run"]["tasks"][-1]
        assert task["route_step"] == "evidence" and task["output_refs"] == [directory]
        assert state["run"]["final"]["output_refs"] == [directory]
        service.runner.llm = ScriptedLLM([
            tool_call("draft_document", confirmed_fact_ids=["f_claimant"], route="litigation",
                      template_id="cn_complaint_traffic_accident", template_version=CURRENT_VERSION,
                      source_refs=[], export_formats=["docx"]),
            tool_call("finish", answer="已生成起诉状草稿，请查看待补字段。"),
        ])
        assert client.post(endpoint + "/routes/litigation/feedback", json={"request_id": "review",
                           "event": "evidence_reviewed", "note": "已核对现有目录，继续准备起诉状"}).status_code == 202
        state = wait(client, conversation)
        task = state["run"]["tasks"][-1]
        assert (task["kind"], task["route_step"], task["status"]) == ("document", "draft", "delivered")
        doc, = state["route_progress"]["artifacts"]["draft"]
        output = next(o for o in state["outputs"] if o["output_id"] == doc)
        artifact = output["structured_data"]["artifacts"]["docx"]
        assert client.get(endpoint + "/artifacts/" + artifact).content.startswith(b"PK")
        assert state["route_progress"]["steps"][-1]["status"] == "pending"
        assert state["route_progress"]["missing"]
        service.runner.llm = ScriptedLLM([
            route("new", "general", "记录实际提交", route_step="submit"),
            tool_call("ask_user", questions=[{"question_id": "filing", "text": "记录提交？",
                                              "field": "litigation_feedback.submitted"}]),
            tool_call("finish", answer="已登记你报告的提交，等法院反馈。"),
        ])
        send(client, conversation, request_id="report", text="我今天已经向法院递交了材料")
        state = wait(client, conversation)
        assert state["route_progress"]["steps"][-1]["status"] == "pending"
        send(client, conversation, request_id="confirm", wait_id=state["run"]["wait"]["wait_id"],
             reply={"text":"确认"})
        state = wait(client, conversation)
        submit = state["route_progress"]["steps"][-1]
        assert submit["status"] == "in_progress" and "已提交" in submit["feedback"]
        assert state["route_progress"]["current_step"] == "submit"


def test_clarification_route_choice_and_stable_receipts(api):
    service, client = api([
        tool_call("ask_user", questions=[{"question_id": "q1", "text": "What is missing?"}]),
        tool_call("request_route_choice", comparison="Choose", options=[{"route": "mediation", "label": "Mediation"}]),
        tool_call("finish", answer="Prepared result, waiting for external action"),
    ])
    with client:
        conversation = create(client)
        first = send(client, conversation, request_id="first", text="Help with my dispute")
        state = wait(client, conversation)
        question = state["run"]["wait"]
        assert state["run"]["status"] == "waiting_user"
        assert client.post(f"/api/conversations/{conversation}/messages",
                           json={"request_id": "new", "text": "wrong kind"}).status_code == 409
        assert client.post(f"/api/conversations/{conversation}/messages", json={
            "request_id": "stale", "wait_id": "old", "reply": {"answers": {"q1": "yes"}}
        }).status_code == 409
        second = send(client, conversation, request_id="answer", wait_id=question["wait_id"],
                      reply={"answers": {"q1": "User answer"}})
        state = wait(client, conversation)
        assert second["run_id"] == first["run_id"]
        menu = state["run"]["wait"]
        send(client, conversation, request_id="choice", wait_id=menu["wait_id"],
             reply={"route_choice": "mediation"})
        state = wait(client, conversation)
        assert state["run"]["status"] == "completed"
        assert state["case"]["selected_route"] == "mediation"
        assert len(state["case"]["statements"]) == 3
        # Historical submissions retain their own wait/result snapshots.
        assert state["messages"][0]["run"]["wait"]["kind"] == "clarification"
        assert state["messages"][1]["run"]["wait"]["kind"] == "route_selection"
        duplicate = send(client, conversation, request_id="first", text="Help with my dispute")
        assert duplicate["run_id"] == first["run_id"]
        assert len(service.runner.llm.calls) == 3
        assert client.post(f"/api/conversations/{conversation}/messages",
                           json={"request_id": "first", "text": "different"}).status_code == 409


def test_followup_history_persists_across_server_restart(api):
    service, client = api([tool_call("finish", answer="Earlier answer with a special reference")])
    with client:
        conversation = create(client)
        first = send(client, conversation, request_id="first", text="Original user message")
        wait(client, conversation)

    def followup(messages, tools):
        assert any(m["role"] == "user" and "Original user message" in m["content"] for m in messages)
        assert "Earlier answer with a special reference" in json.dumps(messages)
        assert messages[-1]["content"] == "Continue the earlier point"
        return tool_call("finish", answer="Followup uses earlier history")

    restarted = Conversations(
        build_runner(llm=ScriptedLLM([followup]), case_service=service.service,
                     real_statutes=False, dotenv=None, store=RunStore(service.runner.store.root),
                     config=RunConfig(conversation_route="case_work")).runner,
        service.service,
    )
    with TestClient(create_app(restarted, token=TOKEN), headers=HEADERS) as client:
        second = send(client, conversation, request_id="second", text="Continue the earlier point")
        state = wait(client, conversation)
        assert second["run_id"] != first["run_id"]
        assert state["run"]["final"]["answer"] == "Followup uses earlier history"
        assert len(state["messages"]) == 2


def test_sse_results_resume_cursor_and_conversation_isolation(api):
    service, client = api([tool_call("finish", answer="SSE final answer")])
    with client:
        conversation = create(client)
        receipt = send(client, conversation, request_id="first", text="hello")
        wait(client, conversation)
        url = f"/api/conversations/{conversation}/events?run_id={receipt['run_id']}"
        response = client.get(url)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "event: run.completed" in response.text and "SSE final answer" in response.text
        event_ids = [line[4:] for line in response.text.splitlines() if line.startswith("id: ")]
        assert event_ids
        replay = client.get(url, headers={"Last-Event-ID": event_ids[-1]})
        assert "event: state" in replay.text and "event: run.completed" not in replay.text
        assert client.get(url, headers={"Last-Event-ID": "another_run:1"}).status_code == 400
        other = create(client)
        assert client.get(f"/api/conversations/{other}/events?run_id={receipt['run_id']}").status_code == 404


def test_file_upload_attachment_context_and_scoped_download(api):
    def inspect_attachment(messages, tools):
        assert "file_" in messages[-1]["content"] and "receipt.txt" in messages[-1]["content"]
        return tool_call("finish", answer="Uploaded file reference received")

    service, client = api([inspect_attachment])
    with client:
        conversation = create(client)
        path = f"/api/conversations/{conversation}"
        upload = client.post(path + "/files", files={"file": ("receipt.txt", b"amount 3000", "text/plain")})
        assert upload.status_code == 201, upload.text
        record = upload.json()
        assert record["size_bytes"] == 11
        assert service.service.assets.get_file(record["case_id"], record["file_id"], "1")[1] == b"amount 3000"
        other = create(client)
        response = client.post(f"/api/conversations/{other}/messages", json={
            "request_id": "forged", "text": "read this",
            "attachments": [{"file_id": record["file_id"]}],
        })
        assert response.status_code == 404
        send(client, conversation, request_id="file", text="Check my file",
             attachments=[{"file_id": record["file_id"]}])
        assert wait(client, conversation)["run"]["status"] == "completed"
        artifact = service.service.assets.put_artifact(record["case_id"], "draft.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document", b"document bytes")
        download = client.get(path + "/artifacts/" + artifact)
        assert download.status_code == 200 and download.content == b"document bytes"
        assert "attachment" in download.headers["content-disposition"]
        assert client.get(f"/api/conversations/{other}/artifacts/{artifact}").status_code == 404
        assert client.post(path + "/files", files={"file": ("bad.exe", b"bad", "application/octet-stream")}).status_code == 415
        assert client.post(path + "/files", files={"file": ("empty.txt", b"", "text/plain")}).status_code == 413


def test_resume_review_uses_an_already_uploaded_file(api, monkeypatch):
    from tests.agent.test_tasks import route

    service, client = api([
        route("new", "item_review", "Check the individual charges", missing=["Itemized file"]),
        tool_call("finish", answer="Please get the itemized bill",
                  task_result={"missing":["Itemized file"]}),
        route("new", "text", "Draft a message"),
        tool_call("finish", answer="Please send the itemized bill.",
                  task_result={"delivered_text":"Please send the itemized bill.", "missing":[]}),
    ], entry="auto")
    with client:
        conversation = create(client)
        send(client, conversation, request_id="total", text="Check the claimed total of 50000")
        first = wait(client, conversation)["run"]
        original = first["tasks"][0]
        store = service.runner.store
        assert store.database == service.repository.path
        assert store.load_checkpoint(first["run_id"]).thread_id == conversation
        # Later submissions must not load a previous transcript for resubmission.
        monkeypatch.setattr(store, "read_messages", lambda run_id: pytest.fail("history copied"))
        send(client, conversation, request_id="message", text="First draft a message asking for details")
        assert wait(client, conversation)["run"]["tasks"][0]["status"] == "paused"
        upload = client.post(f"/api/conversations/{conversation}/files",
                             files={"file":("bill.txt", b"Medical fees 300\nTransport 20", "text/plain")})
        assert upload.status_code == 201
        file_id = upload.json()["file_id"]

        def parse(messages, tools):
            assert file_id in messages[1]["content"]  # From trusted inventory, without reattaching.
            return tool_call("parse_documents", file_ids=[file_id])

        service.runner.llm = ScriptedLLM([
            route("continue", "item_review", task_id=original["task_id"]),
            parse,
            tool_call("finish", answer="Medical fees 300: listed; Transport 20: receipt missing",
                      task_result={"items":[{"quote":"Medical fees 300","finding":"listed"},
                                            {"quote":"Transport 20","finding":"receipt missing"}],
                                   "missing":["Transport receipt"]}),
        ])
        send(client, conversation, request_id="resume", text="The file arrived, continue checking")
        tasks = wait(client, conversation)["run"]["tasks"]
        assert len(tasks) == 2 and tasks[1]["status"] == "delivered"
        task = tasks[0]
        assert task["task_id"] == original["task_id"] and task["status"] == "partial"
        assert task["missing"] == ["Transport receipt"]
        assert service.runner.get_run(first["run_id"]).tasks[0].model_dump(mode="json") == original
        with service.repository.connection() as db:
            count, threads = db.execute("SELECT COUNT(*), COUNT(DISTINCT thread_id) FROM agent_runs "
                                        "WHERE thread_id=?", (conversation,)).fetchone()
        assert (count, threads) == (3, 1)
        assert not list(store.root.glob("*/checkpoint.json"))
        assert json.loads(task["input_refs"][0])["file_id"] == file_id
        assert set(task["reviewed_items"]) == {"Medical fees 300", "Transport 20"}


def test_active_run_conflict_and_idempotent_retry(api):
    entered, release = Event(), Event()

    def blocked(messages, tools):
        entered.set()
        assert release.wait(10)
        return tool_call("finish", answer="done")

    service, client = api([blocked])
    with client:
        conversation = create(client)
        try:
            first = send(client, conversation, request_id="a", text="start")
            assert entered.wait(5)
            duplicate = send(client, conversation, request_id="a", text="start")
            assert duplicate["run_id"] == first["run_id"]
            assert client.post(f"/api/conversations/{conversation}/messages",
                               json={"request_id": "b", "text": "parallel"}).status_code == 409
        finally:
            release.set()
        assert wait(client, conversation)["run"]["status"] == "completed"


def test_oversized_upload_is_rejected(api):
    service, client = api([])
    with client:
        conversation = create(client)
        response = client.post(f"/api/conversations/{conversation}/files",
            files={"file": ("big.txt", b"x" * (MAX_UPLOAD_BYTES + 1), "text/plain")})
        assert response.status_code == 413


def test_capacity_rejection_leaves_no_accepted_message(api):
    service, client = api([], pending_limit=0)
    with client:
        conversation = create(client)
        assert client.post(f"/api/conversations/{conversation}/messages",
                           json={"request_id": "a", "text": "start"}).status_code == 503
        assert client.get(f"/api/conversations/{conversation}").json()["messages"] == []


def test_stale_version_rejected_before_execution(api):
    service, client = api([])
    with client:
        conversation = create(client)
        assert client.post(f"/api/conversations/{conversation}/messages", json={
            "request_id": "a", "text": "start", "expected_case_version": 99,
        }).status_code == 409
        assert service.runner.llm.calls == []


def test_waiting_run_resumes_after_api_restart(api):
    service, client = api([tool_call("ask_user", questions=[{"question_id": "q", "text": "Missing input?"}])])
    with client:
        conversation = create(client)
        first = send(client, conversation, request_id="start", text="Help")
        question = wait(client, conversation)["run"]["wait"]
    setup = build_runner(llm=ScriptedLLM([tool_call("finish", answer="Resumed after restart")]),
        case_service=service.service, real_statutes=False, dotenv=None,
        store=RunStore(service.runner.store.root), config=RunConfig(conversation_route="case_work"))
    restarted = Conversations(setup.runner, service.service)
    with TestClient(create_app(restarted, token=TOKEN), headers=HEADERS) as client:
        receipt = send(client, conversation, request_id="reply", wait_id=question["wait_id"],
                       reply={"answers": {"q": "My answer"}})
        assert receipt["run_id"] == first["run_id"]
        assert wait(client, conversation)["run"]["final"]["answer"] == "Resumed after restart"


def test_second_coordinator_cannot_interrupt_a_live_server(api):
    service, client = api([])
    with client:
        with pytest.raises(RuntimeError, match="one server worker"):
            Conversations(service.runner, service.service)


def test_restart_marks_unfinished_work_failed_without_replay(api):
    from backend.agent.runtime.state import Checkpoint
    from backend.api.schemas import MessageRequest

    service, client = api([])
    with client:
        conversation = create(client)
        case_id = client.get(f"/api/conversations/{conversation}").json()["case_id"]
    run_id = "run_interrupted"
    service.runner.store.save_checkpoint(Checkpoint(
        run_id=run_id, case_id=case_id, config=RunConfig(), status="running",
        case_version=1, stage="consultation",
    ))
    request = MessageRequest(request_id="interrupted", text="Do not replay me")
    with service.repository.connection(write=True) as db:
        db.execute("INSERT INTO api_messages(conversation_id,request_id,run_id,payload,status,created_at) "
                   "VALUES (?,?,?,?,'running','test')",
                   (conversation, request.request_id, run_id,
                    json.dumps(request.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)))
    restarted = Conversations(service.runner, service.service)
    with TestClient(create_app(restarted, token=TOKEN), headers=HEADERS) as client:
        state = client.get(f"/api/conversations/{conversation}").json()
        assert state["run"]["status"] == "failed"
        assert state["messages"][0]["status"] == "failed" and not state["pending"]
        receipt = send(client, conversation, request_id="interrupted", text="Do not replay me")
        assert receipt["run_id"] == run_id and receipt["status"] == "failed"
        assert service.runner.llm.calls == []


@pytest.mark.parametrize(("scope", "task_status"), [("draft", "delivered"), ("complete", "partial")])
def test_backend_drafts_word_and_pdf_and_http_downloads_them(api, scope, task_status):
    from datetime import datetime, timezone
    from backend.case import load_development_case
    from backend.case.models import CaseSnapshot
    from backend.case.drafting.templates import CURRENT_VERSION

    service, client = api([
        LLMResponse(content=json.dumps({"route":"case_work", "reason":"draft requested", "task":{
            "action":"new", "deliverable":"Prepare complaint", "kind":"document",
            "document_scope":scope, "formats":["docx","pdf"]}})),
        tool_call("draft_document", confirmed_fact_ids=["f_claimant"], route="litigation",
                  template_id="cn_complaint_traffic_accident", template_version=CURRENT_VERSION,
                  source_refs=[], export_formats=["docx", "pdf"]),
        tool_call("finish", answer="Draft files are ready for review", task_result={"missing": []}),
    ], entry="auto")
    with client:
        conversation = create(client)
        state = client.get(f"/api/conversations/{conversation}").json()
        raw = load_development_case().model_dump(mode="json")
        raw.update(case_id=state["case_id"], case_version=2, created_at=state["case"]["created_at"], stage="litigation",
                   selected_route="litigation", updated_at=datetime.now(timezone.utc).isoformat())
        service.repository.replace_case(CaseSnapshot.model_validate(raw), expected_case_version=1)
        send(client, conversation, request_id="draft", text=f"Prepare the {scope} complaint in Word and PDF")
        state = wait(client, conversation)
        assert state["run"]["status"] == "completed"
        output, = state["outputs"]
        task, = state["run"]["tasks"]
        assert task["status"] == task_status and task["output_refs"] == [output["output_id"]]
        assert output["missing_fields"]
        reported_fields = [f"文书待补或复核：{field}" for field in output["missing_fields"]]
        assert state["run"]["final"]["open_issues"] == reported_fields
        assert task["missing"] == (reported_fields if scope == "complete" else [])
        artifacts = output["structured_data"]["artifacts"]
        assert set(artifacts) == {"docx", "pdf"}
        for kind, prefix in (("docx", b"PK"), ("pdf", b"%PDF")):
            response = client.get(f"/api/conversations/{conversation}/artifacts/{artifacts[kind]}")
            assert response.status_code == 200 and response.content.startswith(prefix)


def test_unreadable_page_preserves_gap_in_whole_file_review(api):
    from io import BytesIO

    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    from tests.agent.test_tasks import route

    writer = PdfWriter()
    page = writer.add_blank_page(300, 200)
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): DictionaryObject({
            NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })}),
    })
    text = DecodedStreamObject()
    text.set_data(b"BT /F1 12 Tf 10 100 Td (Transport 20) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(text)
    writer.add_blank_page(300, 200)
    buffer = BytesIO()
    writer.write(buffer)
    service, client = api([], entry="auto")
    with client:
        conversation = create(client)
        uploaded = client.post(f"/api/conversations/{conversation}/files",
                               files={"file": ("bill.pdf", buffer.getvalue(), "application/pdf")})
        assert uploaded.status_code == 201
        file_id = uploaded.json()["file_id"]
        service.runner.llm = ScriptedLLM([
            route("new", "item_review", "Review the whole bill", review_file_ids=[file_id]),
            tool_call("parse_documents", file_ids=[file_id], ocr_mode="never"),
            tool_call("finish", answer="Transport 20: matches the first page",
                      task_result={"items": [{"quote": "Transport 20", "finding": "matches the first page"}],
                                   "missing": []}),
        ])
        send(client, conversation, request_id="review", text="Review the whole uploaded bill")
        run = wait(client, conversation)["run"]
        task, = run["tasks"]
        assert task["status"] == "partial"
        assert task["reviewed_items"] == ["Transport 20"]
        assert any("bill.pdf：第 2 页" in issue for issue in task["missing"])
        assert set(task["verified_missing"]) <= set(run["final"]["open_issues"])


@pytest.mark.parametrize(("date", "target_type", "gap"), [
    ("2050-01-01", "property_case_acceptance_fee", "filing_date"),
    ("2024-01-01", "property_case_acceptance_fee", None),
    ("2024-01-01", "principal_balance", "尚未取得针对当前案情的可核验计算结果"),
])
def test_calculation_task_uses_the_requested_tool_result(api, date, target_type, gap):
    from backend.case.calculation.fee_rules import PROPERTY_FEE_RULE_VERSION
    from tests.agent.test_tasks import route

    calculation_type = "property_case_acceptance_fee"
    service, client = api([
        route("new", "calculation", "Calculate the requested amount", calculation_type=target_type),
        tool_call("calculate_costs", calculation_type=calculation_type, rule_version=PROPERTY_FEE_RULE_VERSION,
                  inputs={"claim_value": {"amount": "50000", "currency": "CNY"}, "filing_date": date,
                          "case_category": "ordinary_property", "procedure": "first_instance_ordinary", "adjustment": "none"}),
        tool_call("finish", answer="Estimate ready", task_result={"missing": []}),
    ], entry="auto")
    with client:
        conversation = create(client)
        send(client, conversation, request_id="fee", text=f"Calculate {target_type} using {date}")
        run = wait(client, conversation)["run"]
        task, = run["tasks"]
        if gap:
            assert run["wait"] is None and task["status"] == "partial"
            assert any(gap in issue for issue in task["missing"])
            assert "本轮无法确认" in run["final"]["answer"]
        else:
            assert task["status"] == "delivered"
            assert task["missing"] == []
            assert "预估费用：1050.00 CNY" in run["final"]["answer"]


@pytest.mark.parametrize("route", ["negotiation", "mediation", "litigation"])
def test_all_three_route_selections_save_through_http(api, route):
    service, client = api([
        tool_call("request_route_choice", comparison="Choose a route",
                  options=[{"route": route, "label": route}]),
        tool_call("finish", answer="Selection saved"),
    ])
    with client:
        conversation = create(client)
        send(client, conversation, request_id="start", text="Select a route")
        menu = wait(client, conversation)["run"]["wait"]
        send(client, conversation, request_id="selection", wait_id=menu["wait_id"],
             reply={"route_choice": route})
        state = wait(client, conversation)
        assert state["case"]["stage"] == state["case"]["selected_route"] == route
        assert state["run"]["status"] == "completed"
