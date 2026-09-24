"""Durable, idempotent storage of captured activity (architecture §6.6, §13.1).

A batch is written in one transaction: raw message revisions, attachment
metadata and one processing job per new revision. The learner id always
comes from the verified token, never from the request body.

Idempotency is enforced by the database's unique indexes; the lookups below
only decide whether an event is new, a duplicate, or a new revision. A
per-learner advisory lock serializes concurrent batches from the same
learner so revision numbering cannot race.
"""

from dataclasses import dataclass
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.ingestion.fingerprint import fallback_fingerprint
from app.ingestion.models import EventBatchClientInfo, RawActivityEnvelope
from app.jobs.queue import JOB_PROCESS_RAW_MESSAGE, enqueue_job


class UnknownLearnerError(Exception):
    """The token is valid but no SkillMirror profile exists for its subject."""


class IngestionConflictError(Exception):
    """A unique index rejected an insert the lookups considered new (should not happen)."""


@dataclass(frozen=True)
class StoredEvent:
    event_id: UUID
    status: str  # "accepted" | "duplicate"
    raw_message_id: UUID
    conversation_id: UUID
    revision_index: int


def ingest_batch(
    conn: Connection,
    learner_id: UUID,
    events: list[RawActivityEnvelope],
    client: EventBatchClientInfo,
) -> list[StoredEvent]:
    with conn.transaction():
        conn.execute(
            "select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"ingest:{learner_id}",)
        )
        if (
            conn.execute("select 1 from public.profiles where id = %s", (learner_id,)).fetchone()
            is None
        ):
            raise UnknownLearnerError(str(learner_id))
        return [_ingest_one(conn, learner_id, event, client) for event in events]


def _ingest_one(
    conn: Connection,
    learner_id: UUID,
    event: RawActivityEnvelope,
    client: EventBatchClientInfo,
) -> StoredEvent:
    fingerprint = fallback_fingerprint(
        source_provider=event.source_provider,
        external_conversation_id=event.external_conversation_id,
        role=event.role,
        content_text=event.content_text,
        occurred_at=event.occurred_at,
        captured_at=event.captured_at,
    )

    if event.external_message_id is not None:
        existing = conn.execute(
            """
            select id, conversation_id, revision_index from public.raw_messages
             where learner_id = %s and source_provider = %s::public.source_provider
               and external_message_id = %s and content_hash = %s
            """,
            (learner_id, event.source_provider, event.external_message_id, event.content_hash),
        ).fetchone()
        if existing:
            return StoredEvent(event.event_id, "duplicate", existing[0], existing[1], existing[2])
        # New content under a known message id is a new revision. If the
        # client's revision number is already taken by other content (e.g. the
        # page was reloaded and the client restarted counting), append after
        # the latest stored revision instead of dropping the change.
        (latest,) = conn.execute(
            """
            select max(revision_index) from public.raw_messages
             where learner_id = %s and source_provider = %s::public.source_provider
               and external_message_id = %s
            """,
            (learner_id, event.source_provider, event.external_message_id),
        ).fetchone()
        revision = event.revision_index
        if latest is not None and revision <= latest:
            taken = conn.execute(
                """
                select 1 from public.raw_messages
                 where learner_id = %s and source_provider = %s::public.source_provider
                   and external_message_id = %s and revision_index = %s
                """,
                (learner_id, event.source_provider, event.external_message_id, revision),
            ).fetchone()
            if taken:
                revision = latest + 1
    else:
        existing = conn.execute(
            """
            select id, conversation_id, revision_index from public.raw_messages
             where learner_id = %s and fingerprint = %s and external_message_id is null
            """,
            (learner_id, fingerprint),
        ).fetchone()
        if existing:
            return StoredEvent(event.event_id, "duplicate", existing[0], existing[1], existing[2])
        revision = event.revision_index

    (conversation_id,) = conn.execute(
        """
        insert into public.conversations
            (learner_id, source_provider, external_id, first_seen_at, last_seen_at)
        values (%s, %s::public.source_provider, %s, %s, %s)
        on conflict on constraint conversations_learner_provider_external_key do update
           set first_seen_at = least(public.conversations.first_seen_at, excluded.first_seen_at),
               last_seen_at = greatest(public.conversations.last_seen_at, excluded.last_seen_at)
        returning id
        """,
        (
            learner_id,
            event.source_provider,
            event.external_conversation_id,
            event.captured_at,
            event.captured_at,
        ),
    ).fetchone()

    capture_metadata = {
        "extension_version": client.extension_version,
        "adapter_version": client.adapter_version,
        "client_revision_index": event.revision_index,
    }
    inserted = conn.execute(
        """
        insert into public.raw_messages (
            learner_id, conversation_id, source_provider, source_method,
            external_message_id, external_parent_message_id, message_index, role,
            content_text, content_format, content_hash, fingerprint, revision_index,
            occurred_at, captured_at, provider_model, context_incomplete, active_course_id,
            client_event_uuid, client_event_id, capture_metadata
        ) values (
            %s, %s, %s::public.source_provider, %s::public.source_method,
            %s, %s, %s, %s::public.message_role,
            %s, %s::public.content_format, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s
        )
        on conflict do nothing
        returning id
        """,
        (
            learner_id,
            conversation_id,
            event.source_provider,
            event.source_method,
            event.external_message_id,
            event.external_parent_message_id,
            event.message_index,
            event.role,
            event.content_text,
            event.content_format,
            event.content_hash,
            fingerprint,
            revision,
            event.occurred_at,
            event.captured_at,
            event.provider_model,
            event.context_incomplete,
            event.active_course_id,
            event.event_id,
            event.client_event_id,
            Jsonb(capture_metadata),
        ),
    ).fetchone()
    if inserted is None:
        raise IngestionConflictError(f"unique conflict for event {event.event_id}")
    raw_message_id = inserted[0]

    for position, attachment in enumerate(event.attachment_metadata):
        conn.execute(
            """
            insert into public.attachments
                (raw_message_id, learner_id, position, kind, filename, mime_type, content_available)
            values (%s, %s, %s, %s::public.attachment_kind, %s, %s, %s)
            """,
            (
                raw_message_id,
                learner_id,
                position,
                attachment.kind,
                attachment.filename,
                attachment.mime_type,
                attachment.content_available,
            ),
        )

    enqueue_job(
        conn,
        job_type=JOB_PROCESS_RAW_MESSAGE,
        entity_type="raw_message",
        entity_id=raw_message_id,
        learner_id=learner_id,
    )
    return StoredEvent(event.event_id, "accepted", raw_message_id, conversation_id, revision)


def sync_status(conn: Connection, learner_id: UUID) -> tuple[int, object, int]:
    count, last_received = conn.execute(
        "select count(*), max(received_at) from public.raw_messages where learner_id = %s",
        (learner_id,),
    ).fetchone()
    (pending,) = conn.execute(
        """
        select count(*) from public.processing_jobs
         where learner_id = %s and state in ('PENDING', 'PROCESSING', 'RETRY_WAIT')
        """,
        (learner_id,),
    ).fetchone()
    return count, last_received, pending
