"""Engine 13 - Verification planning (architecture §11.1, Appendix B; ADR 0007). Deterministic, no
model call.

Input: the learner's ACTIVE P5 recommendations of type VERIFY or REVERIFY - never raw AI usage.
Those recommendations already carry the guarded reasoning (actionable debt needs repeated,
accepted, high-confidence delegation; REVERIFY needs a stale or contradicted verification), so:

* one low-impact AI interaction can never produce a plan (it produces no VERIFY recommendation);
* verification never compensates for an uncertain mapping (debt only counts accepted mappings).

A recommended skill is planned when it has no active verification and is not cooling down:

    active session (PLANNED / READY / IN_PROGRESS / SUBMITTED, not failed)   -> skip
    passed within cooldown_after_pass_days (§11.1 "creates a cooldown")      -> skip
    failed within cooldown_after_fail_days                                   -> skip
    abandoned within cooldown_after_abandon_days                             -> skip
    a pipeline failure within retry_after_generation_failure_hours           -> skip

and within the learner's daily burden: at most `max_daily_unsolicited` sessions per learner-day
(Appendix B: 2), counted over every session planned that day in any state - except a session the
learner never started whose skill no longer has an ACTIVE VERIFY / REVERIFY recommendation (P9,
`NOT_NEEDED`): it is not a current check, is never shown as one, and so is no burden. It still
occupies its skill (the one-open-session index), so that skill is not planned twice; should the
skill be recommended for verification again, that same session is the current check again. The
learner's day is their `profiles.timezone` when it is a valid IANA zone, else UTC. Candidates are
taken by recommendation priority, then course importance, then skill id.

Each plan is a PLANNED session plus one GENERATE_VERIFICATION job, in one transaction under the
learner's `verification:{learner}` lock, so a repeated or concurrent planner run creates nothing
new. The same run abandons IN_PROGRESS sessions untouched for abandon_in_progress_after_days
(never failure evidence) and enqueues any missing generation / grading job (idempotent).
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.intelligence.evidence.engine import difficulty_for
from app.intelligence.policy import (
    EvidencePolicy,
    IntelligencePolicy,
    VerificationDifficultyPolicy,
)
from app.jobs.queue import JOB_GENERATE_VERIFICATION, JOB_GRADE_VERIFICATION, enqueue_job

# p9-v1: a not-started session whose skill lost its VERIFY / REVERIFY is no daily burden.
PLANNER_VERSION = "verification-planner/p9-v1"
ENTITY_TYPE = "verification_session"
ACTIVE_STATES = ("PLANNED", "READY", "IN_PROGRESS", "SUBMITTED")
STALE_ABANDON_REASON = "INACTIVE_TIMEOUT"

# SQL predicate over a verification session `s`: the learner's skill currently has an ACTIVE
# VERIFY / REVERIFY recommendation, i.e. SkillMirror still asks for a check of it. A session that
# was never started (PLANNED / READY, no failure) without it is NOT_NEEDED: history, not a check.
SKILL_NEEDS_CHECK_SQL = """exists (
    select 1 from public.recommendations nr
     where nr.learner_id = s.learner_id and nr.skill_id = s.skill_id
       and nr.state = 'ACTIVE' and nr.type in ('VERIFY', 'REVERIFY'))"""
NOT_NEEDED_SQL = f"""(s.state in ('PLANNED', 'READY') and s.failure_code is null
    and not {SKILL_NEEDS_CHECK_SQL})"""


@dataclass(frozen=True)
class Candidate:
    recommendation_id: UUID
    skill_id: UUID
    type: str
    reason_code: str
    priority: int
    mastery_state: str
    debt_band: str | None
    difficulty_band: int | None
    course_id: UUID | None
    importance: float


@dataclass(frozen=True)
class SessionHistory:
    skill_id: UUID
    state: str
    failure_code: str | None
    failed_at: datetime | None
    evaluated_at: datetime | None
    abandoned_at: datetime | None
    passed: bool | None


@dataclass
class PlanReport:
    day: date
    timezone: str
    planned_today: int
    daily_limit: int
    created: list[UUID] = field(default_factory=list)
    abandoned: list[UUID] = field(default_factory=list)
    enqueued: int = 0
    skipped: dict[str, str] = field(default_factory=dict)

    @property
    def remaining_today(self) -> int:
        return max(self.daily_limit - self.planned_today, 0)


def learner_timezone(name: str | None) -> tuple[ZoneInfo, str]:
    """The learner's IANA zone for the day boundary; UTC when missing or invalid."""
    if name:
        try:
            return ZoneInfo(name), name
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return ZoneInfo("UTC"), "UTC"


def plan_difficulty(
    difficulty_band: int | None, *, evidence: EvidencePolicy, policy: VerificationDifficultyPolicy
) -> tuple[float, float, float]:
    """(planned, band min, band max): the skill's difficulty (as evidence normalizes its 1-5
    band) inside the verification bounds."""
    raw = difficulty_for(difficulty_band, evidence)
    planned = min(max(raw, policy.min), policy.max)
    low = max(policy.min, planned - policy.band_half_width)
    high = min(policy.max, planned + policy.band_half_width)
    return round(planned, 6), round(low, 6), round(high, 6)


def blocking_reason(
    history: Iterable[SessionHistory], *, now: datetime, policy: IntelligencePolicy
) -> str | None:
    """Why this skill cannot be planned now (active session or cooldown), or None."""
    p = policy.verification.planner
    for s in history:
        if s.state in ACTIVE_STATES and s.failure_code is None:
            return "ACTIVE_SESSION"
    for s in history:
        if s.failure_code is not None and s.failed_at is not None:
            if now < s.failed_at + timedelta(hours=p.retry_after_generation_failure_hours):
                return "RETRY_AFTER_FAILURE"
        if s.state == "EVALUATED" and s.evaluated_at is not None:
            days = p.cooldown_after_pass_days if s.passed else p.cooldown_after_fail_days
            if now < s.evaluated_at + timedelta(days=days):
                return "COOLDOWN_AFTER_PASS" if s.passed else "COOLDOWN_AFTER_FAIL"
        if s.state == "ABANDONED" and s.abandoned_at is not None:
            if now < s.abandoned_at + timedelta(days=p.cooldown_after_abandon_days):
                return "COOLDOWN_AFTER_ABANDON"
    return None


def select_candidates(
    candidates: Sequence[Candidate], *, blocked: dict[UUID, str], remaining: int
) -> list[Candidate]:
    """Highest recommendation priority first, then course importance, then skill id."""
    ranked = sorted(candidates, key=lambda c: (-c.priority, -c.importance, str(c.skill_id)))
    return [c for c in ranked if c.skill_id not in blocked][: max(remaining, 0)]


_CANDIDATES_SQL = """
select r.id, r.skill_id, r.type::text, r.reason_code, r.priority, r.mastery_state::text,
       r.inputs ->> 'debt_band', n.difficulty_band, cs.course_id, cs.importance
  from public.recommendations r
  join public.skill_nodes n
    on n.id = r.skill_id and n.status = 'ACTIVE' and n.node_kind in ('SKILL', 'SUBSKILL')
  left join lateral (
      select c.course_id, c.importance
        from public.course_skills c
        join public.course_memberships m on m.course_id = c.course_id and m.user_id = r.learner_id and m.role = 'STUDENT'
        join public.courses co on co.id = c.course_id and co.status = 'ACTIVE'
       where c.skill_id = r.skill_id and c.active
       order by c.importance desc, c.course_id
       limit 1) cs on true
 where r.learner_id = %s and r.state = 'ACTIVE' and r.type in ('VERIFY', 'REVERIFY')
"""


def _abandon_stale(conn: Connection, learner_id: UUID, now: datetime, policy) -> list[UUID]:
    cutoff = now - timedelta(days=policy.verification.planner.abandon_in_progress_after_days)
    rows = conn.execute(
        """
        update public.verification_sessions
           set state = 'ABANDONED', abandoned_at = %s, abandon_reason = %s
         where learner_id = %s and state = 'IN_PROGRESS' and started_at < %s
        returning id
        """,
        (now, STALE_ABANDON_REASON, learner_id, cutoff),
    ).fetchall()
    return [r[0] for r in rows]


def _enqueue_missing(conn: Connection, learner_id: UUID) -> int:
    rows = conn.execute(
        """
        select s.id, s.state::text from public.verification_sessions s
         where s.learner_id = %s and s.failure_code is null and s.state in ('PLANNED', 'SUBMITTED')
           and not exists (
               select 1 from public.processing_jobs j
                where j.entity_id = s.id
                  and j.job_type = case s.state when 'PLANNED' then %s else %s end)
        """,
        (learner_id, JOB_GENERATE_VERIFICATION, JOB_GRADE_VERIFICATION),
    ).fetchall()
    for session_id, state in rows:
        enqueue_job(
            conn,
            job_type=JOB_GENERATE_VERIFICATION if state == "PLANNED" else JOB_GRADE_VERIFICATION,
            entity_type=ENTITY_TYPE,
            entity_id=session_id,
            learner_id=learner_id,
        )
    return len(rows)


def plan_verifications(
    conn: Connection,
    learner_id: UUID,
    *,
    policy: IntelligencePolicy,
    now: datetime | None = None,
) -> PlanReport:
    """Turn the learner's ACTIVE VERIFY / REVERIFY recommendations into PLANNED sessions."""
    now = now or datetime.now(UTC)
    planner = policy.verification.planner
    with conn.transaction():
        conn.execute(
            "select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"verification:{learner_id}",)
        )
        abandoned = _abandon_stale(conn, learner_id, now, policy)
        profile = conn.execute(
            "select timezone from public.profiles where id = %s", (learner_id,)
        ).fetchone()
        zone, zone_name = learner_timezone(profile[0] if profile else None)
        day = now.astimezone(zone).date()
        (planned_today,) = conn.execute(
            "select count(*) from public.verification_sessions s "  # noqa: S608 - constant SQL
            f"where s.learner_id = %s and s.plan_day = %s and not {NOT_NEEDED_SQL}",
            (learner_id, day),
        ).fetchone()
        report = PlanReport(day, zone_name, int(planned_today), planner.max_daily_unsolicited)
        report.abandoned = abandoned

        candidates = [
            Candidate(
                recommendation_id=r[0],
                skill_id=r[1],
                type=r[2],
                reason_code=r[3],
                priority=r[4],
                mastery_state=r[5],
                debt_band=r[6],
                difficulty_band=r[7],
                course_id=r[8],
                importance=float(r[9])
                if r[9] is not None
                else policy.skill_graph.default_importance,
            )
            for r in conn.execute(_CANDIDATES_SQL, (learner_id,)).fetchall()
        ]
        history: dict[UUID, list[SessionHistory]] = {}
        for row in conn.execute(
            """
            select s.skill_id, s.state::text, s.failure_code, s.failed_at, s.evaluated_at,
                   s.abandoned_at, r.pass
              from public.verification_sessions s
              left join public.verification_results r on r.session_id = s.id
             where s.learner_id = %s
            """,
            (learner_id,),
        ).fetchall():
            history.setdefault(row[0], []).append(SessionHistory(*row))
        blocked = {
            c.skill_id: reason
            for c in candidates
            if (reason := blocking_reason(history.get(c.skill_id, ()), now=now, policy=policy))
        }
        report.skipped = {str(k): v for k, v in blocked.items()}
        chosen = select_candidates(candidates, blocked=blocked, remaining=report.remaining_today)
        for c in chosen:
            planned, low, high = plan_difficulty(
                c.difficulty_band,
                evidence=policy.evidence,
                policy=policy.verification.difficulty,
            )
            (session_id,) = conn.execute(
                """
                insert into public.verification_sessions (
                    learner_id, course_id, skill_id, recommendation_id, trigger_type, reason_code,
                    planned_difficulty, difficulty_min, difficulty_max, plan_day, plan_timezone,
                    planner_version, planning_inputs
                ) values (%s, %s, %s, %s, %s::public.recommendation_type, %s, %s, %s, %s, %s, %s,
                          %s, %s)
                returning id
                """,
                (
                    learner_id,
                    c.course_id,
                    c.skill_id,
                    c.recommendation_id,
                    c.type,
                    c.reason_code,
                    planned,
                    low,
                    high,
                    day,
                    zone_name,
                    PLANNER_VERSION,
                    Jsonb(
                        {
                            "recommendation_priority": c.priority,
                            "recommendation_reason": c.reason_code,
                            "mastery_state": c.mastery_state,
                            "debt_band": c.debt_band,
                            "importance": round(c.importance, 6),
                            "difficulty_band": c.difficulty_band,
                            "planned_today_before": report.planned_today,
                            "max_daily_unsolicited": planner.max_daily_unsolicited,
                        }
                    ),
                ),
            ).fetchone()
            enqueue_job(
                conn,
                job_type=JOB_GENERATE_VERIFICATION,
                entity_type=ENTITY_TYPE,
                entity_id=session_id,
                learner_id=learner_id,
            )
            report.created.append(session_id)
            report.planned_today += 1
        report.enqueued = _enqueue_missing(conn, learner_id)
    return report
