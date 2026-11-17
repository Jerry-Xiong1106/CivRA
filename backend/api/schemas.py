"""HTTP payloads: user input only; runtime state and trusted IDs stay server-side."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.common.schemas import RunInfo, UserReply
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


class SubmissionView(MessageReceipt):
    input: MessageRequest
    created_at: str


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
