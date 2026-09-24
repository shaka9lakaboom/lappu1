"""Processing-unit assembly (architecture §9.1).

A PROCESS_RAW_MESSAGE job is keyed by one raw message revision. The unit that
gets analysed is a turn: the learner's message plus the assistant response it
triggered, with a bounded window of earlier messages as context only.

* An assistant message is analysed together with its parent user message.
* A user message whose reply already exists defers to the reply's job.
* A user message with no reply yet is retried until the pairing window has
  passed, then analysed alone.
* An assistant message without a user message (orphan) is not analysed.
* A revision superseded by a newer revision of the same message is skipped.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from psycopg import Connection

from app.intelligence.policy import ProcessingUnitPolicy

_MESSAGE_COLUMNS = """
    m.id, m.learner_id, m.conversation_id, m.source_provider::text, m.role::text, m.content_text,
    m.external_message_id, m.external_parent_message_id, m.message_index, m.revision_index,
    m.captured_at, m.context_incomplete, m.active_course_id,
    exists (select 1 from public.attachments a
             where a.raw_message_id = m.id and not a.content_available) as uncaptured_attachments,
    (select count(*) from public.attachments a where a.raw_message_id = m.id) as attachment_count
"""


@dataclass(frozen=True)
class RawMessage:
    id: UUID
    learner_id: UUID
    conversation_id: UUID
    source_provider: str
    role: Literal["user", "assistant"]
    content_text: str
    external_message_id: str | None
    external_parent_message_id: str | None
    message_index: int | None
    revision_index: int
    captured_at: datetime
    context_incomplete: bool
    active_course_id: UUID | None
    uncaptured_attachments: bool
    attachment_count: int


@dataclass(frozen=True)
class ProcessingUnit:
    learner_id: UUID
    conversation_id: UUID
    anchor: RawMessage
    user: RawMessage | None
    assistant: RawMessage | None
    context: list[RawMessage]

    @property
    def source_message_ids(self) -> list[UUID]:
        return [m.id for m in (self.user, self.assistant) if m is not None]

    @property
    def context_incomplete(self) -> bool:
        return any(
            m.context_incomplete or m.uncaptured_attachments
            for m in (self.user, self.assistant)
            if m is not None
        )

    @property
    def attachment_count(self) -> int:
        return sum(m.attachment_count for m in (self.user, self.assistant) if m is not None)

    @property
    def active_course_id(self) -> UUID | None:
        return (self.user or self.anchor).active_course_id


@dataclass(frozen=True)
class UnitDecision:
    """Either a unit to analyse, or a terminal/deferral outcome for the job."""

    unit: ProcessingUnit | None
    outcome: str | None = None
    defer_seconds: int | None = None


def _row_to_message(row: tuple) -> RawMessage:
    return RawMessage(*row)


def load_message(conn: Connection, message_id: UUID) -> RawMessage | None:
    row = conn.execute(
        f"select {_MESSAGE_COLUMNS} from public.raw_messages m where m.id = %s",  # noqa: S608
        (message_id,),
    ).fetchone()
    return _row_to_message(row) if row else None


def _latest_by_external_id(
    conn: Connection, msg: RawMessage, external_id: str, role: str
) -> RawMessage | None:
    row = conn.execute(
        f"""
        select {_MESSAGE_COLUMNS} from public.raw_messages m
         where m.conversation_id = %s and m.learner_id = %s and m.external_message_id = %s
           and m.role = %s::public.message_role
         order by m.revision_index desc limit 1
        """,  # noqa: S608
        (msg.conversation_id, msg.learner_id, external_id, role),
    ).fetchone()
    return _row_to_message(row) if row else None


def _adjacent(
    conn: Connection, msg: RawMessage, direction: Literal["prev", "next"]
) -> RawMessage | None:
    """The nearest message before/after `msg` in rendered order (latest revision)."""
    if msg.message_index is None:
        return None
    op, order = ("<", "desc") if direction == "prev" else (">", "asc")
    row = conn.execute(
        f"""
        select {_MESSAGE_COLUMNS} from public.raw_messages m
         where m.conversation_id = %s and m.learner_id = %s and m.message_index {op} %s
         order by m.message_index {order}, m.revision_index desc limit 1
        """,  # noqa: S608
        (msg.conversation_id, msg.learner_id, msg.message_index),
    ).fetchone()
    return _row_to_message(row) if row else None


def is_superseded(conn: Connection, msg: RawMessage) -> bool:
    if msg.external_message_id is None:
        return False
    return (
        conn.execute(
            """
            select 1 from public.raw_messages
             where learner_id = %s and source_provider = %s::public.source_provider
               and external_message_id = %s and revision_index > %s
             limit 1
            """,
            (msg.learner_id, msg.source_provider, msg.external_message_id, msg.revision_index),
        ).fetchone()
        is not None
    )


def find_parent_user(conn: Connection, assistant: RawMessage) -> RawMessage | None:
    if assistant.external_parent_message_id:
        parent = _latest_by_external_id(
            conn, assistant, assistant.external_parent_message_id, "user"
        )
        if parent:
            return parent
    prev = _adjacent(conn, assistant, "prev")
    return prev if prev is not None and prev.role == "user" else None


def find_reply(conn: Connection, user: RawMessage) -> RawMessage | None:
    if user.external_message_id:
        row = conn.execute(
            f"""
            select {_MESSAGE_COLUMNS} from public.raw_messages m
             where m.conversation_id = %s and m.learner_id = %s
               and m.external_parent_message_id = %s and m.role = 'assistant'
             order by m.revision_index desc, m.captured_at desc limit 1
            """,  # noqa: S608
            (user.conversation_id, user.learner_id, user.external_message_id),
        ).fetchone()
        if row:
            return _row_to_message(row)
    nxt = _adjacent(conn, user, "next")
    return nxt if nxt is not None and nxt.role == "assistant" else None


def recent_context(
    conn: Connection, anchor: RawMessage, policy: ProcessingUnitPolicy
) -> list[RawMessage]:
    if policy.recent_context_messages == 0:
        return []
    if anchor.message_index is not None:
        where, param = "m.message_index < %s", anchor.message_index
    else:
        where, param = "m.captured_at < %s", anchor.captured_at
    rows = conn.execute(
        f"""
        select * from (
            select distinct on (coalesce(m.external_message_id, m.id::text)) {_MESSAGE_COLUMNS}
              from public.raw_messages m
             where m.conversation_id = %s and m.learner_id = %s and {where}
             order by coalesce(m.external_message_id, m.id::text), m.revision_index desc
        ) latest
        order by message_index desc nulls last, captured_at desc
        limit %s
        """,  # noqa: S608
        (anchor.conversation_id, anchor.learner_id, param, policy.recent_context_messages),
    ).fetchall()
    return [_row_to_message(r) for r in reversed(rows)]


def build_unit(
    conn: Connection,
    message_id: UUID,
    policy: ProcessingUnitPolicy,
    *,
    now: datetime | None = None,
) -> UnitDecision:
    msg = load_message(conn, message_id)
    if msg is None:
        return UnitDecision(None, outcome="MESSAGE_NOT_FOUND")
    if is_superseded(conn, msg):
        return UnitDecision(None, outcome="SUPERSEDED_REVISION")

    if msg.role == "assistant":
        user = find_parent_user(conn, msg)
        if user is None:
            return UnitDecision(None, outcome="ORPHAN_ASSISTANT")
        anchor, assistant = user, msg
    else:
        reply = find_reply(conn, msg)
        if reply is not None:
            return UnitDecision(None, outcome="DEFERRED_TO_ASSISTANT")
        now = now or datetime.now(UTC)
        waited = now - msg.captured_at
        if waited < timedelta(seconds=policy.pairing_window_seconds):
            return UnitDecision(
                None, outcome="AWAITING_ASSISTANT", defer_seconds=policy.pairing_retry_seconds
            )
        user, anchor, assistant = msg, msg, None

    return UnitDecision(
        ProcessingUnit(
            learner_id=msg.learner_id,
            conversation_id=msg.conversation_id,
            anchor=anchor,
            user=user,
            assistant=assistant,
            context=recent_context(conn, anchor, policy),
        )
    )


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(limit - 1, 0)] + "…"


def render_context(messages: list[RawMessage], max_chars: int) -> str:
    """Oldest-first transcript of the recent context, trimmed from the oldest end."""
    lines = [
        f"{'Learner' if m.role == 'user' else 'Assistant'}: {m.content_text}" for m in messages
    ]
    text = "\n".join(lines)
    return text if len(text) <= max_chars else "…" + text[-max(max_chars - 1, 0) :]
