"""HTTP payloads: user input only; runtime state and trusted IDs stay server-side."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.common.schemas import LitigationStep, RunInfo, UserReply
from backend.agent.litigation import Feedback
from backend.case.models import BusinessOutput, CaseSnapshot
from backend.case.storage.assets import FileRecord


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateConversation(Payload):
    title: str = Field(default="新案件", min_length=1, max_length=120)


class Attachment(Payload):
    file_id: str = Field(pattern=r"^file_[a-f0-9]{32}$")
    file_version: str = Field(default="1", min_length=1, max_length=100)


class Reply(Payload):
    text: str | None = Field(default=None, min_length=1, max_length=20000)
    answers: dict[str, str] = Field(default_factory=dict, max_length=2)
    route_choice: Literal["negotiation", "mediation", "litigation"] | None = None

    @model_validator(mode="after")
    def has_answer(self):
        if not (self.text or self.answers or self.route_choice):
            raise ValueError("reply needs text, answers or route_choice")
        if any(not key.strip() or not value.strip() or len(value) > 20000
               for key, value in self.answers.items()):
            raise ValueError("answers need nonempty question IDs and bounded text")
        return self

    def as_user_reply(self, files):
        return UserReply(**self.model_dump(), file_ids=[a.file_id for a in files])


class MessageRequest(Payload):
    request_id: str = Field(min_length=1, max_length=128)
    text: str | None = Field(default=None, min_length=1, max_length=20000)
    reply: Reply | None = None
    wait_id: str | None = Field(default=None, min_length=1, max_length=200)
    expected_case_version: int | None = Field(default=None, ge=1, strict=True)
    attachments: list[Attachment] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def valid_kind(self):
        if (self.text is None) == (self.reply is None):
            raise ValueError("provide exactly one of text or reply")
        if (self.reply is not None) != (self.wait_id is not None):
            raise ValueError("reply requires wait_id; new messages cannot provide it")
        return self


class MessageReceipt(BaseModel):
    request_id: str
    run_id: str
    status: str
    run: RunInfo | None = None
    error: str | None = None


class LitigationEntry(Payload):
    request_id: str = Field(min_length=1, max_length=128)
    expected_case_version: int | None = Field(default=None, ge=1, strict=True)
    switch_wait_id: str | None = Field(default=None, min_length=1, max_length=200)


class LitigationFeedback(Payload):
    request_id: str = Field(min_length=1, max_length=128)
    event: Feedback
    note: str = Field(min_length=1, max_length=1500)
    wait_id: str | None = Field(default=None, min_length=1, max_length=200)
    expected_case_version: int | None = Field(default=None, ge=1, strict=True)


class RouteSubmission(MessageRequest):
    operation: Literal["enter_litigation", "litigation_feedback"]
    switch_wait_id: str | None = None
    event: Feedback | None = None


class SubmissionView(MessageReceipt):
    input: RouteSubmission | MessageRequest
    created_at: str


class RouteStepView(BaseModel):
    step_id: LitigationStep
    title: str
    completion_condition: str
    status: str
    feedback: str | None
    event: Feedback | None
    output_refs: list[str]


class LitigationProgressView(BaseModel):
    route: Literal["litigation"]
    role: Literal["plaintiff", "defendant"] | None
    current_step: LitigationStep | None
    next_action: str
    steps: list[RouteStepView]
    artifacts: dict[str, list[str]]
    artifact_gaps: dict[str, list[str]]
    progress_version: int
    task_id: str | None
    task_status: str | None
    missing: list[str]
    wait_id: str | None
    files: list[str]
    inventory_truncated: bool


class ConversationView(BaseModel):
    conversation_id: str
    case_id: str
    title: str
    created_at: str
    pending: bool
    run: RunInfo | None = None
    messages: list[SubmissionView]
    case: CaseSnapshot
    files: list[FileRecord]
    outputs: list[BusinessOutput]
    inventory_truncated: bool = False
    route_progress: LitigationProgressView | None = None
