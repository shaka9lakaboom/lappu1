"""Audit events and admin idempotency (migration 0009, ADR 0008).

Every admin mutation runs in ONE transaction that

    1. serializes on (actor, Idempotency-Key)      pg_advisory_xact_lock
    2. replays: the key's audit event exists       -> the recorded result (same request hash)
                                                   -> IdempotencyConflictError (another request)
    3. applies the mutation
    4. writes its audit event with the key and the request hash

so a retried request never repeats a mutation, and a mutation never exists without its audit
row. Operator scripts (role changes) write OPERATOR events without a key.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.auth.roles import Actor


class IdempotencyConflictError(ValueError):
    """The Idempotency-Key was already used by this admin for a different request."""


@dataclass(frozen=True)
class AuditRecord:
    id: UUID
    action: str
    entity_type: str
    entity_id: UUID
    metadata: dict[str, Any]
    created_at: datetime


def request_hash(route: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps({"route": route, "payload": payload}, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def begin_request(
    conn: Connection, actor: Actor, key: str, request_digest: str
) -> AuditRecord | None:
    """Inside the caller's transaction: lock the key, and return its earlier result if any."""
    conn.execute(
        "select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"admin:{actor.id}:{key}",)
    )
    row = conn.execute(
        """
        select id, action::text, entity_type, entity_id, metadata, created_at, request_hash
          from public.audit_events
         where actor_id = %s and client_request_id = %s
        """,
        (actor.id, key),
    ).fetchone()
    if row is None:
        return None
    if row[6] != request_digest:
        raise IdempotencyConflictError(key)
    return AuditRecord(*row[:6])


def record(
    conn: Connection,
    *,
    actor: Actor | None,
    action: str,
    entity_type: str,
    entity_id: UUID,
    metadata: dict[str, Any],
    key: str | None = None,
    request_digest: str | None = None,
) -> AuditRecord:
    """Write the audit event (a USER actor through the API, None = the operator)."""
    row = conn.execute(
        """
        insert into public.audit_events
            (actor_type, actor_id, actor_role, action, entity_type, entity_id, metadata,
             client_request_id, request_hash)
        values (%s::public.audit_actor_type, %s, %s::public.app_role, %s::public.audit_action,
                %s, %s, %s, %s, %s)
        returning id, action::text, entity_type, entity_id, metadata, created_at
        """,
        (
            "USER" if actor else "OPERATOR",
            actor.id if actor else None,
            actor.role if actor else None,
            action,
            entity_type,
            entity_id,
            Jsonb(metadata),
            key,
            request_digest,
        ),
    ).fetchone()
    return AuditRecord(*row)
