"""Ingestion contract: RawActivityEnvelope and POST /v1/events/batch bodies.

Mirrors packages/contracts/src/raw-activity.ts and
packages/contracts/schemas/event-batch-request.schema.json.

Validation is strict: unknown fields are rejected, strings/ints/bools are not
coerced from other JSON types, and captured text is only ever treated as data.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from app.ingestion.fingerprint import content_hash

MAX_EVENTS_PER_BATCH = 50
MAX_CONTENT_CHARS = 100_000
MAX_ATTACHMENTS_PER_EVENT = 20
MAX_REVISION_INDEX = 10_000
MAX_MESSAGE_INDEX = 100_000
MAX_BODY_BYTES = 6_000_000
# Clock skew tolerated between the extension and the server.
MAX_FUTURE_SKEW = timedelta(minutes=10)

# Providers and methods this endpoint accepts. The contract enum is wider
# (architecture §6.5); only ChatGPT through the extension exists in P1.
INGESTIBLE_PROVIDERS = frozenset({"chatgpt"})
INGESTIBLE_METHODS = frozenset({"browser_extension"})

SourceProvider = Literal["chatgpt", "claude", "gemini", "skillmirror"]
SourceMethod = Literal["browser_extension", "native", "manual"]
MessageRole = Literal["user", "assistant"]
ContentFormat = Literal["text", "markdown"]

ExternalId = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9._:-]{1,256}$")]
VersionTag = Annotated[StrictStr, Field(pattern=r"^[0-9A-Za-z._-]{1,32}$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AttachmentMetadata(_Strict):
    kind: Literal["file", "image", "unknown"]
    filename: Annotated[StrictStr, Field(min_length=1, max_length=255)] | None
    mime_type: Annotated[StrictStr, Field(pattern=r"^[a-z0-9.+-]{1,63}/[a-z0-9.+-]{1,63}$")] | None
    content_available: StrictBool

    @field_validator("filename")
    @classmethod
    def _no_control_chars(cls, value: str | None) -> str | None:
        if value is not None and any(ord(ch) < 32 for ch in value):
            raise ValueError("filename must not contain control characters")
        return value


class RawActivityEnvelope(_Strict):
    event_id: UUID
    schema_version: Literal[1]
    learner_id: UUID
    source_provider: SourceProvider
    source_method: SourceMethod
    external_conversation_id: ExternalId | None
    external_message_id: ExternalId | None
    external_parent_message_id: ExternalId | None
    message_index: Annotated[StrictInt, Field(ge=0, le=MAX_MESSAGE_INDEX)] | None
    role: MessageRole
    content_text: Annotated[StrictStr, Field(min_length=1, max_length=MAX_CONTENT_CHARS)]
    content_format: ContentFormat
    occurred_at: AwareDatetime | None
    captured_at: AwareDatetime
    provider_model: Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9._:/-]{1,100}$")] | None
    revision_index: Annotated[StrictInt, Field(ge=0, le=MAX_REVISION_INDEX)]
    attachment_metadata: Annotated[
        list[AttachmentMetadata], Field(max_length=MAX_ATTACHMENTS_PER_EVENT)
    ]
    context_incomplete: StrictBool
    active_course_id: UUID | None
    content_hash: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
    client_event_id: Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9._:|-]{1,300}$")]

    @field_validator("content_text")
    @classmethod
    def _content_is_storable(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("content_text must not contain NUL characters")
        if not value.strip():
            raise ValueError("content_text must not be blank")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> "RawActivityEnvelope":
        now = datetime.now(UTC)
        if self.captured_at > now + MAX_FUTURE_SKEW:
            raise ValueError("captured_at is in the future")
        if self.occurred_at is not None and self.occurred_at > now + MAX_FUTURE_SKEW:
            raise ValueError("occurred_at is in the future")
        if content_hash(self.content_text) != self.content_hash:
            raise ValueError("content_hash does not match content_text")
        # Never pretend unavailable attachment content was captured (§6.4).
        if any(not a.content_available for a in self.attachment_metadata) and not (
            self.context_incomplete
        ):
            raise ValueError("context_incomplete must be true when attachment content is missing")
        return self


class EventBatchClientInfo(_Strict):
    extension_version: VersionTag
    adapter_version: VersionTag


class EventBatchRequest(_Strict):
    client: EventBatchClientInfo
    events: Annotated[
        list[RawActivityEnvelope], Field(min_length=1, max_length=MAX_EVENTS_PER_BATCH)
    ]

    @model_validator(mode="after")
    def _ingestible(self) -> "EventBatchRequest":
        for index, event in enumerate(self.events):
            if event.source_provider not in INGESTIBLE_PROVIDERS:
                raise ValueError(
                    f"events[{index}]: provider {event.source_provider!r} not supported"
                )
            if event.source_method not in INGESTIBLE_METHODS:
                raise ValueError(f"events[{index}]: method {event.source_method!r} not supported")
        return self


IngestStatus = Literal["accepted", "duplicate"]


class IngestResult(BaseModel):
    event_id: UUID
    status: IngestStatus
    raw_message_id: UUID
    conversation_id: UUID
    revision_index: int


class EventBatchResponse(BaseModel):
    correlation_id: str
    accepted: int
    duplicates: int
    results: list[IngestResult]


class SyncStatusResponse(BaseModel):
    raw_message_count: int
    last_received_at: datetime | None
    pending_jobs: int
