"""Stale-ledger sweep (K1; ADR 0008 H7). Deterministic, no model call.

A ledger row is only recomputed when its skill gets new evidence, so decay (and P6's
NEEDS_REVERIFICATION after a verification ages out) would never reach an inactive skill. The
worker therefore sweeps, at startup and once per UTC day:

    rows computed before today's UTC midnight, or by an older ledger algorithm
      -> recompute_ledger (the same code as the pipeline) + refresh_recommendations

A recompute that changes nothing writes nothing (no ledger_version bump); the sweep then only
advances `computed_as_of`, so a row is visited at most once per UTC day and a large batch of
unchanged rows never starves the rest. A row of an older algorithm (e.g. `ledger/p6-v1` after
the `ledger/p8-v1` debt fix) is always rewritten, so a deployment re-derives what it changed.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from psycopg_pool import ConnectionPool

from app.intelligence.mastery.engine import ALGORITHM_VERSION
from app.intelligence.mastery.ledger import recompute_ledger
from app.intelligence.policy import load_policy
from app.intelligence.recommendations.service import refresh_recommendations

SWEEP_BATCH_ROWS = 500


@dataclass(frozen=True)
class SweepReport:
    learners: int
    skills: int
    remaining: bool  # more stale rows than one batch: the next sweep continues


def utc_day_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def sweep_stale_ledgers(
    pool: ConnectionPool, *, now: datetime | None = None, limit: int = SWEEP_BATCH_ROWS
) -> SweepReport:
    now = now or datetime.now(UTC)
    day = utc_day_start(now)
    with pool.connection() as conn:
        policy = load_policy(conn)
        rows = conn.execute(
            """
            select learner_id, skill_id from public.skill_ledger
             where computed_as_of < %s or algorithm_version is distinct from %s
             order by computed_as_of, learner_id, skill_id
             limit %s
            """,
            (day, ALGORITHM_VERSION, limit + 1),
        ).fetchall()
    remaining = len(rows) > limit
    stale: dict[UUID, list[UUID]] = defaultdict(list)
    for learner_id, skill_id in rows[:limit]:
        stale[learner_id].append(skill_id)
    for learner_id, skill_ids in stale.items():
        with pool.connection() as conn:
            recompute_ledger(conn, learner_id, skill_ids, policy=policy, as_of=now)
            # Unchanged rows kept their old computed_as_of; they were derived again now.
            conn.execute(
                "update public.skill_ledger set computed_as_of = %s "
                "where learner_id = %s and skill_id = any(%s) and computed_as_of < %s",
                (now, learner_id, skill_ids, now),
            )
            refresh_recommendations(conn, learner_id, policy=policy)
    return SweepReport(len(stale), sum(len(s) for s in stale.values()), remaining)
