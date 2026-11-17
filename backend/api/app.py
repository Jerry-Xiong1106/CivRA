"""Frontend HTTP/SSE boundary. Run with uvicorn backend.api.app:app --workers 1."""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.agent.runner import RunConflict, RunStateError, VersionConflict
from backend.case.errors import CaseNotFound
from backend.common.config import ROOT, load_dotenv
from .conversations import CapacityError, Conflict, Conversations
from .schemas import ConversationView, CreateConversation, MessageReceipt, MessageRequest

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
UPLOAD_TYPES = {"application/pdf", "text/plain", "image/png", "image/jpeg", "image/webp",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def build_conversations():
    """Construct real dependencies once at server startup, never per request."""
    from backend.agent.factory import build_runner, llm_from_env
    from backend.agent.model.llm import make_chat_model, response_from_message
    from backend.agent.runtime.store import RunStore
    from backend.case import CaseService, SQLiteCaseRepository
    from backend.case.drafting.documents import MeteredLLMDraftingGateway
    from backend.case.research.knowledge_bridge import CorpusSourceResolver
    from backend.case.review.verification import MeteredLLMReviewGateway
    from backend.common.schemas import RunConfig
    from backend.knowledge.retrieval.service import RetrievalService
    from langchain_core.messages import convert_to_messages

    directory = Path(os.environ.get("CIVRA_API_DATA_DIR", str(ROOT / "var/api")))
    if os.environ.get("DEEPSEEK_API_KEY") and not os.environ.get("CIVRA_LLM_API_KEY"):
        model = make_chat_model(
            model=os.environ.get("CIVRA_LLM_MODEL", "deepseek-flash"),
            base_url=os.environ.get("CIVRA_LLM_BASE_URL", "https://api.deepseek.com"),
            api_key=os.environ["DEEPSEEK_API_KEY"], timeout_s=60,
        ).model_copy(update={"extra_body": {"thinking": {"type": "disabled"}}})
    else:
        model = llm_from_env()

    def call_model(*, messages, max_tokens, temperature):
        started = time.monotonic()
        reply = model.bind(max_tokens=max_tokens, temperature=temperature).invoke(convert_to_messages(messages))
        return response_from_message(reply, model, round((time.monotonic() - started) * 1000))

    service = CaseService(
        SQLiteCaseRepository(directory / "cases.sqlite3"),
        source_resolver=CorpusSourceResolver.from_directory(),
        drafting_gateway=MeteredLLMDraftingGateway(call_model),
        semantic_gateway=MeteredLLMReviewGateway(call_model),
    )
    setup = build_runner(
        llm=model, case_service=service, retrieval_service=RetrievalService.from_config(),
        store=RunStore(directory / "runs"), dotenv=None,
        config=RunConfig(max_steps=40, max_model_calls=12, max_tool_calls=16,
                         max_total_tokens=180000, max_run_seconds=300, max_output_tokens=2200),
    )
    return Conversations(setup.runner, service)


def create_app(conversations: Conversations | None = None, *, token: str | None = None):
    @asynccontextmanager
    async def lifespan(app):
        load_dotenv()
        app.state.token = token if token is not None else os.environ.get("CIVRA_API_TOKEN", "")
        if not app.state.token:
            raise RuntimeError("Set CIVRA_API_TOKEN before starting the API")
        app.state.conversations = conversations if conversations is not None else build_conversations()
        try:
            yield
        finally:
            await asyncio.to_thread(app.state.conversations.close)

    load_dotenv()  # Configuration only; models and stores are initialized in lifespan.
    app = FastAPI(title="CivRA API", version="0.1.0", lifespan=lifespan)
    origins = os.environ.get("CIVRA_API_CORS_ORIGINS", "http://localhost:5173,http://localhost:3000")
    app.add_middleware(
        CORSMiddleware, allow_origins=[s.strip() for s in origins.split(",") if s.strip()],
        allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],
    )
    bearer = HTTPBearer(auto_error=False)

    def authorized(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        # First release is a single trusted workspace, not a multi-user identity system.
        if credentials is None or not secrets.compare_digest(
            credentials.credentials.encode(), request.app.state.token.encode()
        ):
            raise HTTPException(401, "Invalid access token", headers={"WWW-Authenticate": "Bearer"})
        return request.app.state.conversations

    @app.exception_handler(KeyError)
    @app.exception_handler(CaseNotFound)
    async def not_found(request, exc):
        return JSONResponse(status_code=404, content={"detail": "Resource not found"})

    @app.exception_handler(Conflict)
    @app.exception_handler(RunConflict)
    @app.exception_handler(RunStateError)
    @app.exception_handler(VersionConflict)
    async def conflict(request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(CapacityError)
    async def full(request, exc):
        return JSONResponse(status_code=503, content={"detail": str(exc)}, headers={"Retry-After": "2"})

    @app.post("/api/conversations", status_code=201, response_model=ConversationView)
    def create(body: CreateConversation, service=Depends(authorized)):
        return service.create(body.title)

    @app.get("/api/conversations/{conversation_id}", response_model=ConversationView)
    def read(conversation_id: str, service=Depends(authorized)):
        return service.get(conversation_id)

    @app.post("/api/conversations/{conversation_id}/messages", status_code=202, response_model=MessageReceipt)
    def message(conversation_id: str, body: MessageRequest, service=Depends(authorized)):
        return service.submit(conversation_id, body)

    @app.get("/api/conversations/{conversation_id}/events")
    async def events(
        request: Request, conversation_id: str, run_id: str,
        after_seq: int = Query(default=0, ge=0),
        last_event_id: str | None = Header(default=None),
        service=Depends(authorized),
    ):
        service.check_run(conversation_id, run_id)
        if last_event_id:
            try:
                event_run, event_seq = last_event_id.rsplit(":", 1)
                if event_run != run_id or int(event_seq) < 0:
                    raise ValueError
                after_seq = int(event_seq)
            except ValueError:
                raise HTTPException(400, "Last-Event-ID must be this run_id:sequence")

        async def stream():
            cursor = after_seq
            last_ping = time.monotonic()
            while not await request.is_disconnected():
                for event in service.runner.iter_events(run_id, after_seq=cursor):
                    cursor = event.seq
                    data = event.model_dump_json()
                    yield f"id: {run_id}:{cursor}\nevent: {event.type}\ndata: {data}\n\n"
                info = service.check_run(conversation_id, run_id)
                if not service.run_pending(conversation_id, run_id):
                    # Drain events written between the previous read and completion.
                    for event in service.runner.iter_events(run_id, after_seq=cursor):
                        cursor = event.seq
                        yield f"id: {run_id}:{cursor}\nevent: {event.type}\ndata: {event.model_dump_json()}\n\n"
                    # No event ID: reconnect must not advance past a runtime event.
                    data = json.dumps({"run": info.model_dump(mode="json") if info else None,
                                       "pending": False}, ensure_ascii=False)
                    yield f"event: state\ndata: {data}\n\n"
                    return
                if time.monotonic() - last_ping >= 10:
                    yield ": heartbeat\n\n"
                    last_ping = time.monotonic()
                await asyncio.sleep(0.1)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/conversations/{conversation_id}/files", status_code=201)
    async def upload(conversation_id: str, file: UploadFile = File(),
                     document_type: str = Query(default="material", min_length=1, max_length=100),
                     service=Depends(authorized)):
        conversation = service.context(conversation_id)
        try:
            if file.content_type not in UPLOAD_TYPES:
                raise HTTPException(415, "Unsupported media type")
            content = await file.read(MAX_UPLOAD_BYTES + 1)
            if not content or len(content) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, "File must contain 1 byte to 20 MiB")
            filename = (file.filename or "upload").replace("\\", "/").rsplit("/", 1)[-1]
            record = await asyncio.to_thread(
                service.service.assets.put_file, case_id=conversation["case_id"],
                file_id="file_" + uuid4().hex, file_version="1", file_name=filename or "upload",
                media_type=file.content_type, document_type=document_type, content=content,
            )
            return record.model_dump(mode="json")
        finally:
            await file.close()

    @app.get("/api/conversations/{conversation_id}/artifacts/{artifact_id}")
    def download(conversation_id: str, artifact_id: str, service=Depends(authorized)):
        conversation = service.context(conversation_id)
        artifact = service.service.assets.get_artifact(conversation["case_id"], artifact_id)
        filename = artifact["filename"].replace("\\", "/").rsplit("/", 1)[-1]
        return Response(artifact["content"], media_type=artifact["media_type"], headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename, safe=""),
            "X-Content-Type-Options": "nosniff",
        })

    return app


app = create_app()
