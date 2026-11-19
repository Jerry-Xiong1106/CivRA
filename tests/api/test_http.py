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


def components(directory, script, **kwargs):
    directory.mkdir(parents=True, exist_ok=True)
    case_service = CaseService(SQLiteCaseRepository(directory / "cases.sqlite3"))
    setup = build_runner(
        llm=ScriptedLLM(script), case_service=case_service, real_statutes=False, dotenv=None,
        store=RunStore(directory / "runs"), config=RunConfig(),
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
    service, client = api([pick, present])
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
                     config=RunConfig()).runner,
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
        store=RunStore(service.runner.store.root), config=RunConfig())
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
        run_id=run_id, thread_id=conversation, case_id=case_id, config=RunConfig(), status="running",
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


@pytest.mark.parametrize(("date", "target_type", "gap"), [
    ("2050-01-01", "property_case_acceptance_fee", "filing_date"),
    ("2024-01-01", "property_case_acceptance_fee", None),
    ("2024-01-01", "principal_balance", "尚未取得针对当前案情的可核验计算结果"),
])


def select(client, conversation, **payload):
    response = client.post(f"/api/conversations/{conversation}/route", json=payload)
    assert response.status_code == 202, response.text
    return response.json()


def test_clarification_route_entry_and_stable_receipts(api):
    service, client = api([
        tool_call("ask_user", questions=[{"question_id": "q1", "text": "What is missing?"}]),
        tool_call("finish", answer="You can pick the mediation entry"),
        tool_call("confirm_step", step_id="channel", summary="Channel explained", answer="Use the community mediation committee"),
        tool_call("finish", answer="Next: prepare the mediation materials"),
    ])
    with client:
        conversation = create(client)
        first = send(client, conversation, request_id="first", text="Help with my dispute")
        state = wait(client, conversation)
        question = state["run"]["wait"]
        assert state["run"]["status"] == "waiting_user" and question["kind"] == "clarification"
        assert client.post(f"/api/conversations/{conversation}/messages",
                           json={"request_id": "new", "text": "wrong kind"}).status_code == 409
        assert client.post(f"/api/conversations/{conversation}/messages", json={
            "request_id": "stale", "wait_id": "old", "reply": {"answers": {"q1": "yes"}}
        }).status_code == 409
        assert client.post(f"/api/conversations/{conversation}/messages", json={
            "request_id": "bad", "wait_id": question["wait_id"], "reply": {"step_decision": "done"}
        }).status_code == 409
        second = send(client, conversation, request_id="answer", wait_id=question["wait_id"],
                      reply={"answers": {"q1": "User answer"}})
        state = wait(client, conversation)
        assert second["run_id"] == first["run_id"] and state["run"]["status"] == "completed"
        assert state["route_progress"] is None

        select(client, conversation, request_id="route", route="mediation")
        state = wait(client, conversation)
        confirm = state["run"]["wait"]
        assert state["case"]["stage"] == state["case"]["selected_route"] == "mediation"
        assert confirm["kind"] == "step_confirmation" and confirm["step_id"] == "channel"
        assert confirm["message"] == "Use the community mediation committee"
        assert state["route_progress"]["current_step"] == "channel"
        send(client, conversation, request_id="done", wait_id=confirm["wait_id"], reply={"step_decision": "done"})
        state = wait(client, conversation)
        assert state["run"]["status"] == "completed"
        progress = state["route_progress"]
        assert progress["current_step"] == "participate"
        assert [s["status"] for s in progress["steps"]] == ["completed", "pending", "pending"]
        # Start text, clarification answer, route pick and step confirmation are all saved statements.
        assert len(state["case"]["statements"]) == 4
        assert state["messages"][0]["run"]["wait"]["kind"] == "clarification"
        assert state["messages"][2]["input"]["route"] == "mediation"
        duplicate = send(client, conversation, request_id="first", text="Help with my dispute")
        assert duplicate["run_id"] == first["run_id"]
        assert len(service.runner.llm.calls) == 4
        assert client.post(f"/api/conversations/{conversation}/messages",
                           json={"request_id": "first", "text": "different"}).status_code == 409


@pytest.mark.parametrize(("route", "first", "second"), [
    ("negotiation", "prepare", "communicate"), ("mediation", "channel", "participate"),
    ("litigation", "jurisdiction", "evidence"),
])
def test_each_route_entry_guides_steps_in_order(api, route, first, second):
    service, client = api([
        tool_call("confirm_step", step_id=first, summary="first step done", answer="first deliverable"),
        tool_call("confirm_step", step_id=second, summary="second step done", answer="second deliverable"),
    ])
    with client:
        conversation = create(client)
        select(client, conversation, request_id="route", route=route)
        state = wait(client, conversation)
        assert state["case"]["stage"] == route and state["run"]["wait"]["step_id"] == first
        send(client, conversation, request_id="done", wait_id=state["run"]["wait"]["wait_id"],
             reply={"step_decision": "done"})
        state = wait(client, conversation)
        # The Agent moves straight on to the next step in the same run.
        assert state["run"]["wait"]["step_id"] == second
        assert state["route_progress"]["current_step"] == second


def test_revise_keeps_the_step_open(api):
    service, client = api([
        tool_call("confirm_step", step_id="prepare", summary="claims listed", answer="claims v1"),
        tool_call("confirm_step", step_id="prepare", summary="claims revised", answer="claims v2"),
    ])
    with client:
        conversation = create(client)
        select(client, conversation, request_id="route", route="negotiation")
        wait_id = wait(client, conversation)["run"]["wait"]["wait_id"]
        send(client, conversation, request_id="revise", wait_id=wait_id,
             reply={"step_decision": "revise", "text": "add the towing fee"})
        state = wait(client, conversation)
        assert state["run"]["wait"]["message"] == "claims v2"
        assert state["route_progress"]["current_step"] == "prepare"
        assert "add the towing fee" in service.runner.llm.calls[-1]["messages"][-1]["content"]


def test_switching_routes_cancels_the_wait_and_keeps_each_routes_progress(api):
    service, client = api([
        tool_call("confirm_step", step_id="prepare", summary="claims", answer="claims"),
        tool_call("finish", answer="moving to communication"),
        tool_call("confirm_step", step_id="jurisdiction", summary="court", answer="court"),
        tool_call("finish", answer="back to negotiation"),
    ])
    with client:
        conversation = create(client)
        select(client, conversation, request_id="neg", route="negotiation")
        wait_id = wait(client, conversation)["run"]["wait"]["wait_id"]
        send(client, conversation, request_id="done", wait_id=wait_id, reply={"step_decision": "done"})
        wait(client, conversation)
        select(client, conversation, request_id="lit", route="litigation")
        state = wait(client, conversation)
        assert state["route_progress"]["route"] == "litigation" and state["run"]["wait"]["step_id"] == "jurisdiction"
        select(client, conversation, request_id="back", route="negotiation")
        state = wait(client, conversation)
        # The pending litigation confirmation was cancelled; negotiation resumes at its second step.
        litigation_run = state["messages"][-2]["run_id"]
        assert client.get(f"/api/conversations/{conversation}").status_code == 200
        assert service.runner.get_run(litigation_run).status == "cancelled"
        assert state["route_progress"]["current_step"] == "communicate"


def test_backend_drafts_word_and_pdf_and_http_downloads_them(api):
    from datetime import datetime, timezone
    from scripts.case.fixtures import load_development_case
    from backend.case.models import CaseSnapshot
    from backend.case.drafting.templates import CURRENT_VERSION

    service, client = api([
        tool_call("draft_document", confirmed_fact_ids=["f_claimant"], route="litigation",
                  template_id="cn_complaint_traffic_accident", template_version=CURRENT_VERSION,
                  source_refs=[], export_formats=["docx", "pdf"]),
        tool_call("finish", answer="Draft files are ready for review"),
    ])
    with client:
        conversation = create(client)
        state = client.get(f"/api/conversations/{conversation}").json()
        raw = load_development_case().model_dump(mode="json")
        raw.update(case_id=state["case_id"], case_version=2, created_at=state["case"]["created_at"], stage="litigation",
                   selected_route="litigation", updated_at=datetime.now(timezone.utc).isoformat())
        service.repository.replace_case(CaseSnapshot.model_validate(raw), expected_case_version=1)
        send(client, conversation, request_id="draft", text="Prepare the complaint in Word and PDF")
        state = wait(client, conversation)
        assert state["run"]["status"] == "completed"
        output, = state["outputs"]
        assert output["missing_fields"]
        assert state["route_progress"]["steps"][2]["output_refs"] == [output["output_id"]]
        artifacts = output["structured_data"]["artifacts"]
        assert set(artifacts) == {"docx", "pdf"}
        for kind, prefix in (("docx", b"PK"), ("pdf", b"%PDF")):
            response = client.get(f"/api/conversations/{conversation}/artifacts/{artifacts[kind]}")
            assert response.status_code == 200 and response.content.startswith(prefix)

