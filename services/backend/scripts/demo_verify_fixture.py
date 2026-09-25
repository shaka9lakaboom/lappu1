"""The prepared VERIFY example for the judge demo (P9 item 12; manual, never in CI).

The demo must not wait for repeated AI delegation to accumulate live. This prepares ONE disposable,
clearly labelled demo learner on the existing course (default 9440004a) whose captured activity -
synthetic turns, every message starting "[SkillMirror demo data]" - goes through the production
qualification, ledger and recommendation code (no model call). Repeated, confident AI delegation of
one canonical course skill makes its debt actionable, so the recommendation engine ITSELF issues
VERIFY: nothing inserts a recommendation, a verification session or an item. No registry row is
created (existing canonical course skills only). In the demo, opening the Verification Center plans
the check (no model call) and the demo worker writes ONE real Flash-Lite challenge; the learner's
answer is graded deterministically -> VERIFICATION evidence -> ledger / debt / recommendation.

The fixture is sized like the owner-approved P6 hosted acceptance: debt actionable before the
check, and one deterministic pass reaching VERIFIED after it.

    prepare   refused while an earlier demo learner exists; sign the demo learner up (Auth,
              @mailinator.com, display name "SkillMirror demo learner"), STUDENT of the course,
              seed, rebuild its ledger, refresh its queue, check VERIFY; 0 model calls
    status    read only: the demo learner's VERIFY, verification sessions, ledger row
    cleanup   delete the demo learner through the Auth admin API (the normal cascade) and prove
              nothing learner-owned remains

    cd services/backend
    .venv/Scripts/python scripts/demo_verify_fixture.py prepare
    .venv/Scripts/python scripts/demo_verify_fixture.py status
    .venv/Scripts/python scripts/demo_verify_fixture.py cleanup

Hosted values come from services/backend/.env (DATABASE_URL, SUPABASE_URL), apps/web/.env.local
(anon key) and apps/web/.env.e2e.local (service-role key, cleanup only); none is printed. The
demo learner's credentials go to test-results/demo/ (git-ignored). `--database-url`,
`--supabase-url`, `--anon-key`, `--service-key` and `--course` rehearse it on the local stack.
"""

import argparse
import json
import secrets
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import acceptance_p6_hosted as p6  # noqa: E402
from acceptance_p5_hosted import env_value  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.pool import create_pool  # noqa: E402
from app.intelligence.mastery.ledger import rebuild_ledger  # noqa: E402
from app.intelligence.policy import load_policy  # noqa: E402
from app.intelligence.recommendations.service import refresh_recommendations  # noqa: E402
from tests.p6_fixtures import CONFIDENT, seed_turns  # noqa: E402

OUT = REPO / "test-results" / "demo"
STATE = OUT / "demo-learner.json"
PASSWORD = OUT / "demo-learner.password"
LABEL = "[SkillMirror demo data]"
DISPLAY_NAME = "SkillMirror demo learner"


def save(state: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def http(
    url: str, key: str, method: str = "GET", body: dict | None = None, token: str | None = None
):
    request = Request(  # noqa: S310 - the project's own Supabase Auth endpoints
        url,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "apikey": key,
            "Authorization": f"Bearer {token or key}",
            "Content-Type": "application/json",
        },
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310
        raw = response.read()
        return response.status, (json.loads(raw) if raw else {})


def application(n: int, confidence: float) -> dict:
    code = f"answer_{n} = solve(values_{n})"
    return {
        "user_text": f"{LABEL} I wrote this myself for exercise {n}: `{code}` - is it right?",
        "assistant_text": f"{LABEL} Yes, that works.",
        "student_span": code,
        "actor": "STUDENT",
        "evidence_type": "INDEPENDENT_APPLICATION",
        "outcome_signal": "CORRECT",
        "reason_code": "STUDENT_WROTE_CODE",
        "mapping_confidence": confidence,
        "attribution_confidence": confidence,
    }


def delegation(n: int) -> dict:
    code = f"def helper_{n}(items):\n    return sorted(items)"
    return {
        "user_text": f"{LABEL} Write the solution for exercise {n} for me, I don't want to do it myself.",
        "assistant_text": f"{LABEL} Here it is: `{code}`",
        "ai_span": code,
        "actor": "AI",
        "evidence_type": "OBSERVATION",
        "outcome_signal": "NOT_APPLICABLE",
        "reason_code": "AI_WROTE_SOLUTION",
        **CONFIDENT,
    }


def model_runs(pool) -> int:
    return p6.one(pool, "select count(*) from public.model_runs")[0]


def prepare(args, pool) -> int:
    if STATE.exists() and json.loads(STATE.read_text(encoding="utf-8")).get("learner_id"):
        raise SystemExit(
            "a demo learner already exists: run `demo_verify_fixture.py cleanup` first"
        )
    with pool.connection() as conn:
        policy = load_policy(conn)
    # The P6 sizing on this course's canonical skills, with a wider debt margin: the fixture is
    # prepared ahead of the demo, and recency decay must not drop the debt below actionable.
    p6.DEBT_MARGIN = args.debt_margin
    skill = p6.choose_skill(pool, policy)
    runs_before, registry_before = model_runs(pool), p6.registry(pool)

    email = f"skillmirror-demo-{uuid.uuid4().hex[:10]}@mailinator.com"
    password = f"Demo-{secrets.token_urlsafe(15)}"
    status, body = http(
        f"{args.supabase_url}/auth/v1/signup",
        args.anon_key,
        "POST",
        {"email": email, "password": password, "data": {"display_name": DISPLAY_NAME}},
    )
    if status != 200 or not body.get("user"):
        raise SystemExit(f"signup failed ({status})")
    learner = body["user"]["id"]
    save({"learner_id": learner, "email": email, "course_id": args.course})  # cleanup can find it
    OUT.mkdir(parents=True, exist_ok=True)
    PASSWORD.write_text(password, encoding="utf-8")

    with pool.connection() as conn, conn.transaction():
        conn.execute(
            "insert into public.course_memberships (course_id, user_id, role) "
            "values (%s, %s, 'STUDENT') on conflict do nothing",
            (args.course, learner),
        )
    skill_id = uuid.UUID(skill["skill_id"])
    plan = [(skill_id, application(n, skill["confidence"])) for n in range(skill["applications"])]
    plan += [(skill_id, delegation(n)) for n in range(skill["delegations"])]
    now = datetime.now(UTC)
    seed_turns(pool, uuid.UUID(learner), plan, policy=policy, now=now)
    with pool.connection() as conn:
        rebuild_ledger(conn, uuid.UUID(learner), policy=policy, as_of=now)
        refresh_recommendations(conn, uuid.UUID(learner), policy=policy)
    rec = p6.one(
        pool,
        "select type::text, reason_code from public.recommendations "
        "where learner_id = %s and skill_id = %s and state = 'ACTIVE'",
        learner,
        skill_id,
    )
    ledger = p6.one(
        pool,
        "select mastery_state::text, debt_eligible, round(debt_score::numeric, 2) "
        "from public.skill_ledger where learner_id = %s and skill_id = %s",
        learner,
        skill_id,
    )
    ok = (
        rec is not None
        and rec[0] == "VERIFY"
        and model_runs(pool) == runs_before
        and p6.registry(pool) == registry_before
    )
    save(
        {
            "learner_id": learner,
            "email": email,
            "course_id": args.course,
            "skill": skill,
            "prepared_at": now.isoformat(),
            "recommendation": rec,
            "ledger": ledger,
            "model_runs_created": model_runs(pool) - runs_before,
        }
    )
    print(f"demo learner: {email} (password in {PASSWORD.relative_to(REPO)})")
    print(
        f"skill: {skill['name']} ({skill['applications']} applications, {skill['delegations']} delegations)"
    )
    print(
        f"ledger: {ledger}; active recommendation: {rec}; model runs created: 0; registry unchanged"
    )
    print(f"prepare: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def status(_args, pool) -> int:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    learner, skill = state["learner_id"], state["skill"]["skill_id"]
    with pool.connection() as conn:
        conn.execute("set transaction read only")
        rec = conn.execute(
            "select type::text, reason_code from public.recommendations "
            "where learner_id = %s and skill_id = %s and state = 'ACTIVE'",
            (learner, skill),
        ).fetchone()
        sessions = conn.execute(
            "select state::text, failure_code from public.verification_sessions "
            "where learner_id = %s order by created_at",
            (learner,),
        ).fetchall()
        ledger = conn.execute(
            "select mastery_state::text, debt_eligible, round(debt_score::numeric, 2), "
            "debt_components ->> 'verification' from public.skill_ledger "
            "where learner_id = %s and skill_id = %s",
            (learner, skill),
        ).fetchone()
        conn.rollback()
    print(f"demo learner {state['email']} · skill {state['skill']['name']}")
    print(f"active recommendation: {rec}; sessions: {sessions}; ledger: {ledger}")
    return 0


def cleanup(args, pool) -> int:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    learner = state["learner_id"]
    status_code, _ = http(
        f"{args.supabase_url}/auth/v1/admin/users/{learner}", args.service_key, "DELETE"
    )
    remaining = p6.learner_rows(pool, learner)
    auth_user = p6.one(pool, "select count(*) from auth.users where id = %s", learner)[0]
    ok = status_code == 200 and auth_user == 0 and set(remaining.values()) == {0}
    print(f"admin delete {status_code}; auth user {auth_user}; learner rows {remaining}")
    if ok:
        STATE.unlink(missing_ok=True)
        PASSWORD.unlink(missing_ok=True)
    print(f"cleanup: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("phase", choices=("prepare", "status", "cleanup"))
    parser.add_argument("--course", default=p6.COURSE)
    parser.add_argument("--database-url")
    parser.add_argument("--supabase-url")
    parser.add_argument("--anon-key")
    parser.add_argument("--service-key")
    parser.add_argument("--debt-margin", type=float, default=2.0)
    args = parser.parse_args()
    settings = get_settings()
    args.supabase_url = (args.supabase_url or str(settings.supabase_url)).rstrip("/")
    if args.phase == "prepare":
        args.anon_key = args.anon_key or env_value(
            REPO / "apps" / "web" / ".env.local", "NEXT_PUBLIC_SUPABASE_ANON_KEY"
        )
    if args.phase == "cleanup":
        args.service_key = args.service_key or env_value(
            REPO / "apps" / "web" / ".env.e2e.local", "SUPABASE_SERVICE_ROLE_KEY"
        )
    p6.COURSE = args.course  # choose_skill reads the course from the P6 module
    url = args.database_url or settings.database_url.get_secret_value()
    pool = create_pool(url, max_size=3)
    try:
        return {"prepare": prepare, "status": status, "cleanup": cleanup}[args.phase](args, pool)
    finally:
        pool.close()


if __name__ == "__main__":
    raise SystemExit(main())
