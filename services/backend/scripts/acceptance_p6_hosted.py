"""Hosted P6 acceptance on the existing course graph with ONE real challenge generation (manual gate;
never run in CI). Run only after migration 0008 is approved, pushed and verified on hosted.

One disposable learner, no registry row, the real P3B/P4 learner untouched (hashed before and
after), and a planned live budget of 1 VERIFICATION_GENERATION request, 0 evaluation and 0
embedding requests (a deterministically graded challenge). Phases:

    inspect  read-only: open jobs (must be none), the policy, and the course skill the fixture
             will use (highest importance whose type list offers MCQ / numeric, and for which a
             fixture exists that makes debt actionable AND lets one pass reach VERIFIED)
    prepare  snapshot; sign up the disposable learner; seed learner-owned evidence on that skill
             (production qualification + ledger + recommendations, no model call); check the
             VERIFY recommendation; GET /v1/verifications plans ONE session (no model call)
    worker   the P6 worker on the REAL Gemini gateway, registered for GENERATE_VERIFICATION and
             GRADE_VERIFICATION only (it can never run another learner's pending turn job). It
             generates the challenge, then keeps running (grading) during the walkthrough
             and the replay; stop it with Ctrl+C or --seconds
    ready    wait for READY; check the validator report and the live request count; write the
             walkthrough credentials + the correct answer (read server-side) to git-ignored files
    (then)   apps/web/e2e/p6-acceptance.spec.ts: start, refresh/resume, submit, result, VERIFIED
    verify   result, evidence, provenance, ledger, debt, recommendation, idempotent replay of the
             submission and of both jobs (0 new model requests), the request budget
    cleanup  delete the disposable learner through the Auth admin API (the normal cascade) and
             prove nothing learner-owned, no registry change and an unchanged real learner

    cd services/backend
    # API: services/backend/.env with GEMINI_API_KEY= (empty) and WORKER_ENABLED=false (port 8001)
    # worker (runtime overrides only, never committed):
    #   GEMINI_GENERATION_MODEL=gemini-3.5-flash-lite GEMINI_ROUTINE_MODEL=gemini-3.5-flash-lite
    #   GEMINI_GENERATION_RPM=12 MODEL_QUOTA_RESERVE=25
    #   MODEL_DAILY_REQUEST_LIMITS=gemini-3.5-flash-lite=500,gemini-3.7-flash=20,gemini-3.8-flash=20
    .venv/Scripts/python scripts/acceptance_p6_hosted.py inspect
    .venv/Scripts/python scripts/acceptance_p6_hosted.py prepare --api http://127.0.0.1:8001
    .venv/Scripts/python scripts/acceptance_p6_hosted.py worker --seconds 1800     # background
    .venv/Scripts/python scripts/acceptance_p6_hosted.py ready
    (browser walkthrough)
    .venv/Scripts/python scripts/acceptance_p6_hosted.py verify --api http://127.0.0.1:8001
    .venv/Scripts/python scripts/acceptance_p6_hosted.py cleanup

Connection values come from services/backend/.env (DATABASE_URL, SUPABASE_URL, GEMINI_API_KEY for
the worker only), apps/web/.env.local (anon key) and apps/web/.env.e2e.local (service-role key,
cleanup only); none is printed or written. State and evidence go to test-results/p6-hosted/
(git-ignored).
"""

import argparse
import json
import secrets
import sys
import threading
import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from acceptance_p5 import Http  # noqa: E402
from acceptance_p5_hosted import (  # noqa: E402
    Report,
    env_value,
    one,
    real_snapshot,
    registry,
    rows,
    sign_in,
)

from app.core.config import get_settings  # noqa: E402
from app.db.pool import create_pool  # noqa: E402
from app.intelligence.debt.engine import compute_debt  # noqa: E402
from app.intelligence.evidence.engine import difficulty_for  # noqa: E402
from app.intelligence.mastery.engine import EvidenceRecord, compute_mastery  # noqa: E402
from app.intelligence.mastery.ledger import rebuild_ledger  # noqa: E402
from app.intelligence.policy import IntelligencePolicy, load_policy  # noqa: E402
from app.intelligence.recommendations.service import refresh_recommendations  # noqa: E402
from app.intelligence.verification.jobs import (  # noqa: E402
    mark_generation_failed,
    mark_grading_failed,
    run_generation_job,
    run_grading_job,
)
from app.intelligence.verification.planner import plan_difficulty  # noqa: E402
from app.jobs.queue import JOB_GENERATE_VERIFICATION, JOB_GRADE_VERIFICATION  # noqa: E402
from app.jobs.worker import Worker  # noqa: E402
from app.model_gateway import build_gateway  # noqa: E402
from tests.p6_fixtures import application_turn, delegation_turn, seed_turns  # noqa: E402

COURSE = "9440004a-a25e-4e15-94c0-17c21f6bd695"
OUT = REPO / "test-results" / "p6-hosted"
STATE = OUT / "state.json"
DETERMINISTIC = ("mcq", "numeric")
LEARNER_TABLES = (
    "conversations",
    "raw_messages",
    "processing_jobs",
    "activity_segments",
    "mapping_decisions",
    "skill_mappings",
    "attributions",
    "evidence_events",
    "skill_ledger",
    "recommendations",
    "verification_sessions",
    "verification_items",
    "verification_results",
)


def save(state: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def load() -> dict:
    return json.loads(STATE.read_text(encoding="utf-8"))


def requests_by_task(pool) -> dict[str, int]:
    """Provider requests (cache hits excluded) by task type."""
    return {
        task: n
        for task, n in rows(
            pool,
            "select task_type, count(*) from public.model_runs where cache_source_run_id is null "
            "group by 1",
        )
    }


def embedding_requests(pool) -> int:
    return one(
        pool,
        "select count(*) from public.model_runs where task_type like 'EMBED%%' "
        "and cache_source_run_id is null",
    )[0]


def learner_rows(pool, learner: str) -> dict[str, int]:
    counts = {
        t: one(pool, f"select count(*) from public.{t} where learner_id = %s", learner)[0]  # noqa: S608
        for t in LEARNER_TABLES
    }
    counts["course_memberships"] = one(
        pool, "select count(*) from public.course_memberships where user_id = %s", learner
    )[0]
    counts["profiles"] = one(pool, "select count(*) from public.profiles where id = %s", learner)[0]
    return counts


# --- The fixture: the smallest learner-owned evidence that makes the loop meaningful -------------


def _application(strength_confidence: float, multiplier: float) -> EvidenceRecord:
    return EvidenceRecord(
        id=uuid.uuid4(), skill_id=uuid.UUID(int=0), source_type="AI_ACTIVITY",
        evidence_type="INDEPENDENT_APPLICATION", actor="STUDENT", outcome_signal="CORRECT",
        outcome=1.0, strength=multiplier * strength_confidence, evidence_confidence=strength_confidence,
        occurred_at=datetime.now(UTC), learning_relevance="high", mapping_status="ACCEPTED",
    )  # fmt: skip


def _delegation(confidence: float) -> EvidenceRecord:
    return EvidenceRecord(
        id=uuid.uuid4(), skill_id=uuid.UUID(int=0), source_type="AI_ACTIVITY",
        evidence_type="OBSERVATION", actor="AI", outcome_signal="NOT_APPLICABLE", outcome=None,
        strength=0.0, evidence_confidence=confidence, occurred_at=datetime.now(UTC),
        rationale_code="AI_WROTE_SOLUTION", learning_relevance="high", mapping_status="ACCEPTED",
    )  # fmt: skip


DELEGATION_CONFIDENCE = 0.95  # delegation_turn's mapping / attribution confidence
# Margins so a UTC day boundary during the run (1 day of recency decay) cannot flip a gate.
DEBT_MARGIN, SUPPORT_MARGIN, MEAN_MARGIN = 0.3, 0.05, 0.005


def pass_strength(difficulty: float, policy: IntelligencePolicy) -> float:
    ev = policy.evidence
    return ev.base_weights["VERIFICATION"] * (
        ev.difficulty.multiplier_base + ev.difficulty.multiplier_slope * difficulty
    )


def fixture_for(importance: float, band: int | None, policy: IntelligencePolicy) -> dict | None:
    """The smallest learner-owned fixture such that, through the production engines, the debt is
    actionable before the check AND one deterministic pass reaches VERIFIED.

    Two strengths are tuned: the application confidence (0.80-0.95, the accepted range: it scales
    each application's strength) and the number of delegations. The design point is a pass at the
    LOWEST difficulty of the planned band (robust to the model's choice inside the band); when no
    such fixture exists, a pass at the planned difficulty (`ready` re-checks the actual item)."""
    ev = policy.evidence
    multiplier = ev.difficulty.multiplier_base + ev.difficulty.multiplier_slope * difficulty_for(
        band, ev
    )
    planned, low, _ = plan_difficulty(band, evidence=ev, policy=policy.verification.difficulty)
    reverification = policy.verification.reverification
    now = datetime.now(UTC)
    confidences = [round(0.95 - 0.005 * i, 3) for i in range(31)]  # 0.95 .. 0.80
    for design, difficulty in (("band_minimum", low), ("planned", planned)):
        check = replace(
            _application(1.0, pass_strength(difficulty, policy)),
            source_type="VERIFICATION",
            evidence_type="VERIFICATION",
        )
        for applications in range(1, 5):
            for delegations in range(2, 15):
                delegated = [_delegation(DELEGATION_CONFIDENCE) for _ in range(delegations)]
                for confidence in confidences:
                    records = [_application(confidence, multiplier) for _ in range(applications)]
                    records += delegated
                    before = compute_mastery(
                        records, policy.mastery, now, reverification=reverification
                    )
                    debt = compute_debt(
                        records, before, importance=importance, policy=policy.debt, as_of=now
                    )
                    if (
                        debt.score < policy.debt.actionable_min_score + DEBT_MARGIN
                        or not debt.eligible
                    ):
                        continue
                    after = compute_mastery(
                        [*records, check], policy.mastery, now, reverification=reverification
                    )
                    if (
                        after.state == "VERIFIED"
                        and after.support >= policy.mastery.verified_min_support + SUPPORT_MARGIN
                        and after.mastery_mean >= policy.mastery.verified_min_mean + MEAN_MARGIN
                    ):
                        return {
                            "applications": applications,
                            "delegations": delegations,
                            "confidence": confidence,
                            "state_before": before.state,
                            "debt_before": round(debt.score, 3),
                            "design": design,
                            "design_difficulty": difficulty,
                            "pass_strength": round(pass_strength(difficulty, policy), 4),
                            "support_after_pass": round(after.support, 4),
                            "mean_after_pass": round(after.mastery_mean, 4),
                        }
    return None


def choose_skill(pool, policy: IntelligencePolicy) -> dict:
    candidates = rows(
        pool,
        """
        select n.id, n.canonical_name, n.difficulty_band, n.assessment_types, cs.importance
          from public.course_skills cs
          join public.skill_nodes n on n.id = cs.skill_id
         where cs.course_id = %s and cs.active and n.status = 'ACTIVE'
           and n.node_kind in ('SKILL', 'SUBSKILL')
         order by cs.importance desc, n.difficulty_band desc nulls last, n.canonical_name
        """,
        COURSE,
    )
    offered = [c for c in candidates if set(c[3]) & set(DETERMINISTIC)]

    def described(row, plan: dict) -> dict:
        skill_id, name, band, types, importance = row
        return {
            "skill_id": str(skill_id),
            "name": name,
            "band": band,
            "types": list(types),
            "importance": float(importance),
            **plan,
        }

    for row in offered:
        plan = fixture_for(float(row[4]), row[2], policy)
        if plan:
            return described(row, plan)
    # No skill lets ONE pass cross the VERIFIED gates while its debt is still actionable (the two
    # pull in opposite directions by design). Then seed only the actionable debt: the live pass is
    # shown NOT to verify on its own, and deterministic independent applications seeded after it
    # (while the pass is recent) show that VERIFIED becomes reachable once every gate is met.
    for row in offered:
        plan = debt_fixture_for(float(row[4]), row[2], policy)
        if plan:
            return described(row, plan)
    raise SystemExit(
        "no course skill offers MCQ/numeric with actionable-debt fixture: stop and report"
    )


def debt_fixture_for(
    importance: float, band: int | None, policy: IntelligencePolicy
) -> dict | None:
    ev = policy.evidence
    multiplier = ev.difficulty.multiplier_base + ev.difficulty.multiplier_slope * difficulty_for(
        band, ev
    )
    now = datetime.now(UTC)
    for applications in range(1, 4):
        for delegations in range(2, 15):
            records = [_application(0.95, multiplier) for _ in range(applications)]
            records += [_delegation(DELEGATION_CONFIDENCE) for _ in range(delegations)]
            before = compute_mastery(
                records, policy.mastery, now, reverification=policy.verification.reverification
            )
            debt = compute_debt(
                records, before, importance=importance, policy=policy.debt, as_of=now
            )
            if debt.eligible and debt.score >= policy.debt.actionable_min_score + DEBT_MARGIN:
                return {
                    "applications": applications,
                    "delegations": delegations,
                    "confidence": 0.95,
                    "state_before": before.state,
                    "debt_before": round(debt.score, 3),
                    "design": "complete_after_pass",
                }
    return None


def expected_after_pass(pool, learner: str, skill: str, difficulty: float, policy) -> str:
    """The mastery state a deterministic pass of this item gives, from the learner's real
    evidence (the same engine the ledger uses)."""
    from app.intelligence.mastery.ledger import load_records

    with pool.connection() as conn:
        records = load_records(conn, uuid.UUID(learner), [uuid.UUID(skill)]).get(
            uuid.UUID(skill), []
        )
    check = replace(
        _application(1.0, pass_strength(difficulty, policy)),
        source_type="VERIFICATION",
        evidence_type="VERIFICATION",
    )
    return compute_mastery(
        [*records, check],
        policy.mastery,
        datetime.now(UTC),
        reverification=policy.verification.reverification,
    ).state


def inspect(_args, pool, *_unused) -> Report:
    report = Report()
    with pool.connection() as conn:
        policy = load_policy(conn)  # fails loudly without 0008 (the verification key)
    open_jobs = rows(
        pool,
        "select job_type, state::text, count(*) from public.processing_jobs "
        "where state not in ('COMPLETED', 'FAILED') group by 1, 2",
    )
    p6 = one(pool, "select count(*) from public.verification_sessions")[0]
    report.check(
        "I",
        "no open job anywhere, no verification session yet",
        not open_jobs and p6 == 0,
        {"open_jobs": open_jobs, "verification_sessions": p6},
    )
    skill = choose_skill(pool, policy)
    report.check("I", "fixture skill of the existing course (no registry row needed)", True, skill)
    save({"inspect": report.results, "skill": skill})
    return report


def prepare(args, pool, supabase_url: str, anon: str) -> Report:
    report = Report()
    state = load()
    skill = state["skill"]
    owner = str(one(pool, "select owner_id from public.courses where id = %s", COURSE)[0])
    state |= {
        "real_learner_id": owner,
        "real_snapshot": real_snapshot(pool, owner),
        "requests": requests_by_task(pool),
        "embedding_requests": embedding_requests(pool),
        "registry": registry(pool),
        "started_at": datetime.now(UTC).isoformat(),
    }
    open_jobs = one(
        pool,
        "select count(*) from public.processing_jobs where state not in ('COMPLETED', 'FAILED')",
    )[0]
    assert open_jobs == 0, f"{open_jobs} open jobs on hosted: stop (the worker must only run ours)"

    supabase = Http(supabase_url, apikey=anon)
    email = f"skillmirror-p6-accept-{uuid.uuid4().hex[:12]}@mailinator.com"
    password = f"P6-{secrets.token_urlsafe(18)}"
    status, body = supabase.call("POST", "/auth/v1/signup", {"email": email, "password": password})
    assert status == 200 and body.get("access_token"), f"signup failed ({status})"
    learner, token = body["user"]["id"], body["access_token"]
    state |= {"learner_id": learner, "email": email}
    save(state)  # from here on, cleanup can always find the learner
    (OUT / "ui-learner.password").write_text(password, encoding="utf-8")

    with pool.connection() as conn, conn.transaction():
        conn.execute(
            "insert into public.course_memberships (course_id, user_id, role) "
            "values (%s, %s, 'STUDENT') on conflict do nothing",
            (COURSE, learner),
        )
    with pool.connection() as conn:
        policy = load_policy(conn)
    confidence = {
        "mapping_confidence": skill["confidence"],
        "attribution_confidence": skill["confidence"],
    }
    skill_id = uuid.UUID(skill["skill_id"])
    plan = [
        (skill_id, application_turn(f"answer_{n} = solve(values_{n})", **confidence))
        for n in range(skill["applications"])
    ]
    plan += [
        (skill_id, delegation_turn(f"def helper_{n}(items):\n    return sorted(items)", n))
        for n in range(skill["delegations"])
    ]
    now = datetime.now(UTC)
    seed_turns(pool, uuid.UUID(learner), plan, policy=policy, now=now)
    with pool.connection() as conn:
        rebuild_ledger(conn, uuid.UUID(learner), policy=policy, as_of=now)
        refresh_recommendations(conn, uuid.UUID(learner), policy=policy)
    api = Http(args.api, token)
    entry = next(
        s
        for s in api.get(f"/v1/ledger?course_id={COURSE}")["skills"]
        if s["skill_id"] == skill["skill_id"]
    )
    rec = next(
        (
            r
            for r in api.get(f"/v1/recommendations?course_id={COURSE}")["recommendations"]
            if r["skill_id"] == skill["skill_id"]
        ),
        None,
    )
    report.check(
        "1",
        "VERIFY recommendation exists (repeated unverified delegation, actionable debt)",
        rec is not None
        and rec["type"] == "VERIFY"
        and entry["debt_actionable"]
        and registry(pool) == state["registry"],
        {
            "skill": skill["name"],
            "state": entry["mastery_state"],
            "debt": entry["debt_score"],
            "band": entry["debt_band"],
            "registry": registry(pool),
        },
    )

    before = requests_by_task(pool)
    queue = api.get("/v1/verifications")
    again = api.get("/v1/verifications")
    report.check(
        "2",
        "planner creates exactly one session (no model call in the request)",
        len(queue["preparing"]) == 1
        and len(again["preparing"]) == 1
        and queue["preparing"][0]["recommendation_id"] == rec["id"]
        and requests_by_task(pool) == before,
        {"session": queue["preparing"][0]["id"], "budget": queue["budget"]},
    )
    state |= {
        "session_id": queue["preparing"][0]["id"],
        "recommendation_id": rec["id"],
        "ledger_before": entry,
        "prepare": report.results,
    }
    save(state)
    return report


def p6_worker(pool, gateway) -> Worker:
    """The P6 jobs only: this worker cannot pick up any other job type."""
    return Worker(
        pool,
        {
            JOB_GENERATE_VERIFICATION: partial(run_generation_job, pool, gateway),
            JOB_GRADE_VERIFICATION: partial(run_grading_job, pool, gateway),
        },
        failure_hooks={
            JOB_GENERATE_VERIFICATION: lambda job, e, final: mark_generation_failed(
                pool, job, e, final
            ),
            JOB_GRADE_VERIFICATION: lambda job, e, final: mark_grading_failed(pool, job, e, final),
        },
        batch_size=2,
    )


def worker(args, pool, *_unused) -> Report:
    settings = get_settings()
    if args.fake_provider:
        # Local rehearsal only: the scripted fake provider of acceptance_p6.py (no real model).
        from acceptance_p6 import fake_gateway, scripted_provider

        gateway = fake_gateway(pool, scripted_provider())
    else:
        gateway = build_gateway(settings, pool)
    if gateway is None:
        raise SystemExit("GEMINI_API_KEY is not set for the worker")
    print("routing:", gateway.routing.name, gateway.routing.model_for("VERIFICATION_GENERATION"))
    for status in gateway.budget_status():
        print(f"budget {status.model}: {status.used}/{status.limit} used, reserve {status.reserve}")
    stop = threading.Event()
    runner = p6_worker(pool, gateway)
    thread = threading.Thread(target=runner.run_forever, args=(stop, 2.0), daemon=True)
    thread.start()
    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    stop.set()
    thread.join(timeout=30)
    print("worker stopped:", runner.stats)
    return Report()


def ready(_args, pool, *_unused) -> Report:
    report = Report()
    state = load()
    session = state["session_id"]
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        row = one(
            pool,
            "select state::text, failure_code from public.verification_sessions where id = %s",
            session,
        )
        if row[0] != "PLANNED" or row[1]:
            break
        time.sleep(3)
    row = one(
        pool,
        """
        select s.state::text, s.failure_code, s.generation_attempts, i.assessment_type::text,
               i.expected_answer, i.validation, i.generation_model_run_id, m.model, m.provider,
               m.status::text, m.prompt_version, i.difficulty, s.difficulty_min, s.difficulty_max
          from public.verification_sessions s
          left join public.verification_items i on i.session_id = s.id
          left join public.model_runs m on m.id = i.generation_model_run_id
         where s.id = %s
        """,
        session,
    )
    now = requests_by_task(pool)
    generation = now.get("VERIFICATION_GENERATION", 0) - state["requests"].get(
        "VERIFICATION_GENERATION", 0
    )
    report.check(
        "3",
        "real Gemini challenge generated, validator accepted it, session READY",
        row[0] == "READY" and row[5] is not None and all(row[5]["checks"].values()),
        {
            "state": row[0],
            "attempts": row[2],
            "type": row[3],
            "model": row[7],
            "provider": row[8],
            "prompt_version": row[10],
            "difficulty": row[11],
            "band": [row[12], row[13]],
            "checks": row[5]["checks"] if row[5] else None,
            "generation_requests": generation,
        },
    )
    report.check(
        "4",
        "deterministically gradeable type (no evaluation call will be needed)",
        row[3] in DETERMINISTIC,
        row[3],
    )
    if row[3] == "mcq":
        answer = {"selected": sorted(k.strip() for k in row[4].split(","))}
    else:
        answer = {"text": row[4]}
    with pool.connection() as conn:
        policy = load_policy(conn)
    # What a pass of THIS item gives, predicted by the ledger's own engine on the real evidence.
    state_after = expected_after_pass(
        pool, state["learner_id"], state["skill"]["skill_id"], row[11], policy
    )
    report.check(
        "4b",
        "predicted state after a pass of this item",
        state_after == "VERIFIED" or state["skill"]["design"] == "complete_after_pass",
        {
            "design": state["skill"]["design"],
            "item_difficulty": row[11],
            "state_after": state_after,
        },
    )
    expect = OUT / "ui-learner.expect.json"
    expect.write_text(
        json.dumps(
            {
                "skill_id": state["skill"]["skill_id"],
                "skill_name": state["skill"]["name"],
                "state_before": state["ledger_before"]["mastery_state"],
                "state_after": state_after,
                "answer": answer,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    password = (OUT / "ui-learner.password").read_text(encoding="utf-8")
    (OUT / "ui-learner.env").write_text(
        f"P6_ACCEPTANCE_EMAIL={state['email']}\nP6_ACCEPTANCE_PASSWORD={password}\n"
        f"P6_ACCEPTANCE_EXPECT={expect.resolve().as_posix()}\n"
        f"P6_ACCEPTANCE_OUT={OUT.resolve().as_posix()}\n",
        encoding="utf-8",
    )
    state |= {
        "ready": report.results,
        "generation_requests_at_ready": generation,
        "state_after": state_after,
    }
    save(state)
    return report


def verify(args, pool, supabase_url: str, anon: str) -> Report:
    report = Report()
    state = load()
    learner, session, skill = state["learner_id"], state["session_id"], state["skill"]["skill_id"]
    token = sign_in(
        Http(supabase_url, apikey=anon),
        state["email"],
        (OUT / "ui-learner.password").read_text(encoding="utf-8"),
    )
    # The backend verifies `iat` without leeway; this machine's clock can trail Supabase Auth's by
    # about a second, so a token used at once may be "not yet valid" (a known P0 limitation).
    time.sleep(3)
    api = Http(args.api, token)
    detail = api.get(f"/v1/verifications/{session}")
    result = detail["session"]["result"]
    report.check(
        "5",
        "submission persisted, deterministic grader produced the result",
        detail["session"]["state"] == "EVALUATED"
        and result
        and result["passed"]
        and result["evaluator_type"] == "DETERMINISTIC",
        {
            "status": detail["session"]["status"],
            "response": detail["response"],
            "score": result and result["score"],
        },
    )

    # The submission's idempotency, replayed with the key the browser used.
    key, response = one(
        pool,
        "select submission_idempotency_key, submitted_response from public.verification_sessions where id = %s",
        session,
    )
    same = api.call(
        "POST", f"/v1/verifications/{session}/submit", response, {"Idempotency-Key": key}
    )
    other = (
        {"selected": ["A" if response["selected"] != ["A"] else "B"]}
        if "selected" in response
        else {"answer": "1" if response.get("answer") == "0" else "0"}
    )
    different = api.call(
        "POST", f"/v1/verifications/{session}/submit", other, {"Idempotency-Key": key}
    )
    report.check(
        "6",
        "same key + same answer replays; same key + different answer is 409",
        same[0] == 200 and same[1]["created"] is False and different[0] == 409,
        {"replay": same[0], "different": different[0]},
    )

    evidence = one(
        pool,
        """
        select e.id, e.source_type::text, e.source_id, e.actor::text, e.evidence_type::text,
               e.independence, e.grading_confidence, e.strength, e.attribution_id, e.mapping_id,
               e.segment_id, e.raw_message_ids, i.id, s.id, s.recommendation_id, s.course_id,
               s.skill_id, (select count(*) from public.model_runs m where m.id = any(e.model_run_ids)),
               cardinality(e.model_run_ids)
          from public.evidence_events e
          join public.verification_results r on r.id = e.source_id
          join public.verification_items i on i.id = r.item_id
          join public.verification_sessions s on s.id = i.session_id
         where e.learner_id = %s and e.source_type = 'VERIFICATION'
        """,
        learner,
    )
    report.check(
        "7",
        "VERIFICATION EvidenceEvent with complete provenance",
        evidence is not None
        and str(evidence[2]) == result["id"]
        and evidence[3:7] == ("STUDENT", "VERIFICATION", 1.0, 1.0)
        and evidence[8:12] == (None, None, None, [])
        and str(evidence[13]) == session
        and str(evidence[14]) == state["recommendation_id"]
        and str(evidence[15]) == COURSE
        and str(evidence[16]) == skill
        and evidence[17] == evidence[18] == 1,
        {
            "evidence": str(evidence[0]) if evidence else None,
            "strength": evidence and evidence[7],
            "chain": "evidence -> result -> item -> session -> recommendation / course / skill",
            "model_runs": evidence and evidence[17],
        },
    )

    before = state["ledger_before"]
    after = next(
        s for s in api.get(f"/v1/ledger?course_id={COURSE}")["skills"] if s["skill_id"] == skill
    )
    report.check(
        "8a",
        "ledger recomputed from the new evidence (the state the engine predicted)",
        after["mastery_state"] == state["state_after"]
        and after["ledger_version"] > before["ledger_version"],
        {
            "state": f"{before['mastery_state']} -> {after['mastery_state']}",
            "mean": round(after["mastery_mean"], 3),
            "support": round(after["support"], 3),
            "ledger_version": f"{before['ledger_version']} -> {after['ledger_version']}",
        },
    )
    if after["mastery_state"] != "VERIFIED":
        # The pass alone does not verify below the gates; independent applications seeded after
        # it (deterministic, learner-owned) complete them while the pass is recent.
        report.check(
            "8b",
            "a pass alone does not verify a skill short of the mean / support gates",
            after["support"] < 4.0 or after["mastery_mean"] < 0.8,
            {"mean": round(after["mastery_mean"], 3), "support": round(after["support"], 3)},
        )
        complete_gates(pool, learner, skill, state)
        after = next(
            s for s in api.get(f"/v1/ledger?course_id={COURSE}")["skills"] if s["skill_id"] == skill
        )
    skill_detail = api.get(f"/v1/skills/{skill}")
    report.check(
        "8",
        "VERIFIED reached with every gate met (recent pass, mean >= 0.80, support >= 4.0)",
        after["mastery_state"] == "VERIFIED"
        and after["mastery_mean"] >= 0.8
        and after["support"] >= 4.0
        and all(g["met"] for g in skill_detail["mastery"]["gates"]),
        {
            "state": after["mastery_state"],
            "mean": round(after["mastery_mean"], 3),
            "support": round(after["support"], 3),
            "explanation": skill_detail["mastery"]["explanation_code"],
        },
    )
    factor = next(
        (f["value"] for f in skill_detail["debt"]["factors"] if f["code"] == "VERIFICATION"), None
    )
    report.check(
        "9",
        "debt verification factor drops to recently-passed",
        skill_detail["debt"]["verification"] == "RECENTLY_PASSED"
        and factor == 0.2
        and not after["debt_actionable"],
        {"debt": f"{before['debt_score']} -> {after['debt_score']}", "factor": factor},
    )
    old = one(
        pool,
        "select state::text from public.recommendations where id = %s",
        state["recommendation_id"],
    )[0]
    report.check(
        "10",
        "recommendation refreshed",
        old == "COMPLETED"
        and skill_detail["recommendation"]["type"] == "NO_ACTION"
        and skill_detail["recommendation"]["reason_code"] == "RECENTLY_VERIFIED",
        {"verify": old, "now": skill_detail["recommendation"]["reason_code"]},
    )

    # Replay: the queue again and both jobs again -> nothing new, no model request.
    counts = learner_rows(pool, learner)
    requests = requests_by_task(pool)
    api.get("/v1/verifications")
    with pool.connection() as conn:
        conn.execute(
            "update public.processing_jobs set state = 'PENDING', available_at = now() "
            "where entity_id = %s and learner_id = %s",
            (session, learner),
        )
    deadline = time.monotonic() + 120
    while (
        time.monotonic() < deadline
        and one(
            pool,
            "select count(*) from public.processing_jobs where entity_id = %s and state <> 'COMPLETED'",
            session,
        )[0]
    ):
        time.sleep(3)
    outcomes = rows(
        pool,
        "select job_type, outcome from public.processing_jobs where entity_id = %s order by 1",
        session,
    )
    report.check(
        "11",
        "replay creates nothing and makes 0 new model requests",
        learner_rows(pool, learner) == counts
        and requests_by_task(pool) == requests
        and dict(outcomes)
        == {
            JOB_GENERATE_VERIFICATION: "ALREADY_ISSUED",
            JOB_GRADE_VERIFICATION: "ALREADY_EVALUATED",
        },
        {"outcomes": outcomes},
    )

    final = requests_by_task(pool)
    delta = {
        k: final.get(k, 0) - state["requests"].get(k, 0)
        for k in set(final) | set(state["requests"])
    }
    delta = {k: v for k, v in delta.items() if v}
    report.check(
        "12",
        "live budget: 1 generation, 0 evaluation, 0 embedding requests",
        delta.get("VERIFICATION_GENERATION", 0) >= 1
        and "VERIFICATION_EVALUATION" not in delta
        and embedding_requests(pool) == state["embedding_requests"],
        {"requests_by_task": delta},
    )
    report.check(
        "R",
        "real P3B/P4 learner rows unchanged",
        real_snapshot(pool, state["real_learner_id"]) == state["real_snapshot"],
        state["real_snapshot"][:16],
    )
    state["verify"] = report.results
    save(state)
    return report


def complete_gates(pool, learner: str, skill: str, state: dict) -> None:
    """Seed the fewest learner-owned independent applications that complete the VERIFIED gates
    (simulated with the production engine first), then recompute and refresh."""
    from app.intelligence.mastery.ledger import load_records, recompute_ledger

    with pool.connection() as conn:
        policy = load_policy(conn)
        records = load_records(conn, uuid.UUID(learner), [uuid.UUID(skill)])[uuid.UUID(skill)]
    ev = policy.evidence
    multiplier = ev.difficulty.multiplier_base + ev.difficulty.multiplier_slope * difficulty_for(
        state["skill"]["band"], ev
    )
    now = datetime.now(UTC)
    reverification = policy.verification.reverification
    for extra in range(1, 11):
        simulated = [*records, *(_application(0.95, multiplier) for _ in range(extra))]
        result = compute_mastery(simulated, policy.mastery, now, reverification=reverification)
        if result.state == "VERIFIED":
            break
    else:
        raise SystemExit("the gates cannot be completed with 10 applications: stop and report")
    plan = [
        (
            uuid.UUID(skill),
            application_turn(
                f"checked_{n} = solve(new_values_{n})",
                mapping_confidence=0.95,
                attribution_confidence=0.95,
            ),
        )
        for n in range(extra)
    ]
    seed_turns(pool, uuid.UUID(learner), plan, policy=policy, now=now)
    with pool.connection() as conn:
        recompute_ledger(conn, uuid.UUID(learner), [uuid.UUID(skill)], policy=policy, as_of=now)
        refresh_recommendations(conn, uuid.UUID(learner), policy=policy)
    state["completion_applications"] = extra
    save(state)


def cleanup(_args, pool, supabase_url: str, _anon: str) -> Report:
    import urllib.request

    report = Report()
    state = load()
    learner = state["learner_id"]
    service_key = _args.service_key or env_value(
        REPO / "apps" / "web" / ".env.e2e.local", "SUPABASE_SERVICE_ROLE_KEY"
    )
    request = urllib.request.Request(  # noqa: S310 - the project's own Auth admin endpoint
        f"{supabase_url.rstrip('/')}/auth/v1/admin/users/{learner}",
        method="DELETE",
        headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        status = response.status
    remaining = learner_rows(pool, learner)
    auth_user = one(pool, "select count(*) from auth.users where id = %s", learner)[0]
    report.check(
        "C",
        "disposable learner deleted through the Auth cascade; nothing learner-owned remains",
        status == 200 and auth_user == 0 and set(remaining.values()) == {0},
        {"admin_delete": status, "auth_user": auth_user, **remaining},
    )
    report.check(
        "C",
        "registry and course overlay unchanged",
        registry(pool) == state["registry"],
        registry(pool),
    )
    report.check(
        "C",
        "real P3B/P4 learner rows unchanged",
        real_snapshot(pool, state["real_learner_id"]) == state["real_snapshot"],
        state["real_snapshot"][:16],
    )
    for name in ("ui-learner.password", "ui-learner.env"):
        (OUT / name).unlink(missing_ok=True)
    state["cleanup"] = report.results
    save(state)
    return report


PHASES = {
    "inspect": inspect,
    "prepare": prepare,
    "worker": worker,
    "ready": ready,
    "verify": verify,
    "cleanup": cleanup,
}


def main() -> int:
    global COURSE
    parser = argparse.ArgumentParser(description="SkillMirror P6 hosted acceptance")
    parser.add_argument("phase", choices=tuple(PHASES))
    parser.add_argument("--api", default="http://127.0.0.1:8001")
    parser.add_argument("--seconds", type=int, default=1800)
    # Local rehearsal only (the defaults are the hosted run): another course, the local keys,
    # and the scripted fake provider instead of Gemini.
    parser.add_argument("--course", default=COURSE)
    parser.add_argument("--anon-key", default=None)
    parser.add_argument("--service-key", default=None)
    parser.add_argument("--fake-provider", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    supabase_url = str(settings.supabase_url).rstrip("/")
    COURSE = args.course
    print(f"target: {supabase_url} course {COURSE}")
    anon = args.anon_key or env_value(
        REPO / "apps" / "web" / ".env.local", "NEXT_PUBLIC_SUPABASE_ANON_KEY"
    )
    pool = create_pool(settings.database_url.get_secret_value(), max_size=4)
    try:
        report = PHASES[args.phase](args, pool, supabase_url, anon)
    finally:
        pool.close()
    if args.phase != "worker":
        evidence = OUT / f"evidence-{args.phase}.json"
        evidence.write_text(
            json.dumps({"passed": report.passed, "results": report.results}, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"P6 HOSTED {args.phase.upper()}:", "PASS" if report.passed else "FAIL")
        return 0 if report.passed else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
