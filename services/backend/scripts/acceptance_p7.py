"""P7 acceptance: the teacher overview and the admin operations over real HTTP (manual gate; never
run in CI). No model call at all.

Targets
    hosted (default)  only after migration 0009 is approved, pushed and verified on hosted. The
                      source of the fixture skills is the existing course 9440004a: its canonical
                      skills are REUSED (no new skill_nodes / aliases / edges); the real P3B/P4
                      learner (that course's owner) is only read, and hashed before and after.
    --local-graph     a local rehearsal (give it on EVERY phase): the source course is a P5
                      fixture graph seeded for a throw-away local account (prefixed registry rows:
                      `supabase db reset` afterwards). The script refuses a target that does not
                      match SUPABASE_URL.

Every account is disposable (Auth signup; @mailinator.com on hosted) and deleted at cleanup
through the Auth admin API (the normal cascade). Learner-owned rows only, plus two synthetic
skill_candidates (the REJECT path) that cleanup deletes. Audit events are kept: they are the
record of the admin actions (their actor ids become null when the accounts are deleted).

    prepare  snapshot (model runs, registry, real learner); sign up 3 students, a teacher, an
             outsider teacher and an admin; operator role grants (grant_role.change_role, audited);
             a disposable 3-student course reusing 5 canonical skills of the source course and a
             2-student course (suppression); P5 evidence for the students (production code, no
             model call); memberships through POST /v1/admin/courses/{id}/members; two FAILED
             jobs and two PENDING candidates (one each for the browser, one each for verify);
             the walkthrough credentials (git-ignored files)
    (then)   apps/web/e2e/p7-acceptance.spec.ts: teacher pages, admin pages (retry + reject in the
             browser), a student's forbidden panel
    verify   roles from the database, forged metadata, the authorization matrix, exact cohort
             aggregates, suppression, privacy, N2, admin reads, retry / review / enrollment
             idempotency, audit rows, zero model calls, registry and real learner unchanged
    cleanup  delete the candidates and the accounts; prove nothing learner-owned remains

    cd services/backend
    # API: services/backend/.env with GEMINI_API_KEY= (empty) and WORKER_ENABLED=false (port 8001)
    .venv/Scripts/python scripts/acceptance_p7.py prepare --api http://127.0.0.1:8001
    (browser walkthrough, see apps/web/e2e/p7-acceptance.spec.ts)
    .venv/Scripts/python scripts/acceptance_p7.py verify --api http://127.0.0.1:8001
    .venv/Scripts/python scripts/acceptance_p7.py cleanup

Connection values come from the environment / services/backend/.env (DATABASE_URL, SUPABASE_URL),
apps/web/.env.local (anon key) and apps/web/.env.e2e.local (service-role key, cleanup only), or
--anon-key / --service-key; none is printed or written. State and evidence go to
test-results/p7-<target>/ (git-ignored).
"""

import argparse
import json
import secrets
import sys
import time
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from acceptance_p5 import Http  # noqa: E402
from acceptance_p5_hosted import (  # noqa: E402
    ROLE_NAMES,
    Report,
    env_value,
    one,
    real_snapshot,
    rows,
    sign_in,
)
from grant_role import change_role  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.pool import create_pool  # noqa: E402
from app.intelligence.skill_graph.canonical import skill_key  # noqa: E402
from tests.p5_fixtures import ROLES, seed_graph, seed_p5_learner_on_course  # noqa: E402

HOSTED_COURSE = "9440004a-a25e-4e15-94c0-17c21f6bd695"
ACCOUNTS = ("student1", "student2", "student3", "teacher", "outsider", "admin")
STATES = ("UNKNOWN", "EMERGING", "DEVELOPING", "DEMONSTRATED", "VERIFIED", "NEEDS_REVERIFICATION")
EXPECTED_STATES = {
    "for_loops": "DEMONSTRATED",
    "while_loops": "UNKNOWN",
    "comprehensions": "UNKNOWN",
    "variables": "EMERGING",
    "indexing": "DEVELOPING",
}
FORBIDDEN_KEYS = {
    "learner_id",
    "user_id",
    "email",
    "display_name",
    "debt_score",
    "debt_band",
    "debt_eligible",
    "actor",
    "mastery_mean",
}
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
)
ZERO_ID = "00000000-0000-4000-8000-000000000001"

OUT = REPO / "test-results" / "p7-hosted"


def state_file() -> Path:
    return OUT / "state.json"


def save(state: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    state_file().write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def load() -> dict:
    return json.loads(state_file().read_text(encoding="utf-8"))


def model_runs(pool) -> dict[str, int]:
    kinds = dict(
        rows(
            pool,
            "select case when task_type like 'EMBED%%' then 'embedding' else 'generation' end, "
            "count(*) from public.model_runs group by 1",
        )
    )
    return {"generation": kinds.get("generation", 0), "embedding": kinds.get("embedding", 0)}


def registry_counts(pool) -> list[int]:
    return list(
        one(
            pool,
            "select (select count(*) from public.skill_nodes), (select count(*) from public.skill_edges), "
            "(select count(*) from public.skill_aliases)",
        )
    )


def learner_rows(pool, user: str) -> dict[str, int]:
    counts = {
        t: one(pool, f"select count(*) from public.{t} where learner_id = %s", user)[0]  # noqa: S608
        for t in LEARNER_TABLES
    }
    counts["course_memberships"] = one(
        pool, "select count(*) from public.course_memberships where user_id = %s", user
    )[0]
    counts["courses_owned"] = one(
        pool, "select count(*) from public.courses where owner_id = %s", user
    )[0]
    counts["profiles"] = one(pool, "select count(*) from public.profiles where id = %s", user)[0]
    return counts


def attributable_runs(pool, state: dict) -> int:
    """Model runs of the acceptance's accounts, courses or jobs (must stay 0: P7 is model-free)."""
    return one(
        pool,
        "select count(*) from public.model_runs where learner_id = any(%s::uuid[]) "
        "or course_id = any(%s::uuid[]) or processing_job_id = any(%s::uuid[])",
        [a["id"] for a in state.get("accounts", {}).values()],
        [c for c in (state.get("course"), state.get("small_course")) if c],
        list(state.get("jobs", {}).values()),
    )[0]


def keys_in(value, found: set[str] | None = None) -> set[str]:
    found = set() if found is None else found
    if isinstance(value, dict):
        found.update(value)
        for v in value.values():
            keys_in(v, found)
    elif isinstance(value, list):
        for v in value:
            keys_in(v, found)
    return found


def sign_up(supabase: Http, label: str, domain: str) -> dict:
    email = f"skillmirror-p7-{label}-{uuid.uuid4().hex[:10]}@{domain}"
    password = f"P7-{secrets.token_urlsafe(18)}"
    status, body = supabase.call("POST", "/auth/v1/signup", {"email": email, "password": password})
    assert status == 200 and body.get("access_token"), (
        f"signup failed ({status}); is email confirmation off?"
    )
    return {"id": body["user"]["id"], "email": email, "password": password}


def token(supabase: Http, account: dict) -> str:
    return sign_in(supabase, account["email"], account["password"])


def teacher_routes(course: str) -> list[tuple[str, str]]:
    return [
        ("GET", "/v1/teacher/courses"),
        ("GET", f"/v1/teacher/courses/{course}/overview"),
        ("GET", "/v1/admin/overview"),
        ("GET", "/v1/admin/jobs"),
        ("GET", f"/v1/admin/jobs/{ZERO_ID}"),
        ("POST", f"/v1/admin/jobs/{ZERO_ID}/retry"),
        ("GET", "/v1/admin/model-runs"),
        ("GET", "/v1/admin/skill-candidates"),
        ("POST", f"/v1/admin/skill-candidates/{ZERO_ID}/review"),
        ("GET", "/v1/admin/benchmark"),
        ("GET", f"/v1/admin/benchmark/{ZERO_ID}"),
        ("GET", "/v1/admin/courses"),
        ("GET", f"/v1/admin/courses/{course}"),
        ("POST", f"/v1/admin/courses/{ZERO_ID}/members"),
        ("GET", "/v1/admin/skills"),
        ("GET", f"/v1/admin/skills/{ZERO_ID}"),
    ]


def call(api: str, bearer: str | None, method: str, path: str, body=None, key: str | None = None):
    headers = {"Idempotency-Key": key or uuid.uuid4().hex} if method == "POST" else {}
    return Http(api, bearer).call(method, path, body if method == "POST" else None, headers)


# --- prepare -----------------------------------------------------------------------------------


def disposable_course(pool, owner: str, name: str, roles: dict[str, str], source: str) -> str:
    """A learner-owned course whose overlay reuses the source course's canonical skills."""
    with pool.connection() as conn, conn.transaction():
        (course,) = conn.execute(
            """
            insert into public.courses (owner_id, name, subject, level, graph_status, graph_version,
                                        graph_generated_at)
            values (%s, %s, 'Python', 'Beginner', 'READY', 1, now()) returning id
            """,
            (owner, name),
        ).fetchone()
        for skill in roles.values():
            conn.execute(
                """
                insert into public.course_skills (course_id, skill_id, importance, source, graph_version)
                select %s, cs.skill_id, cs.importance, 'MANUAL', 1 from public.course_skills cs
                 where cs.course_id = %s and cs.skill_id = %s
                """,
                (course, source, skill),
            )
    return str(course)


def failed_job(pool, learner: str) -> str:
    """One of the learner's seeded (completed, attributed) raw-message jobs, marked FAILED with an
    error that carries secret-looking text the admin API must redact. Replaying it is 0 requests:
    the turn is analysed and attributed already."""
    secret = "AIza" + "S" * 35
    with pool.connection() as conn:
        (job,) = conn.execute(
            """
            update public.processing_jobs set state = 'FAILED', attempts = max_attempts,
                   last_error = %s, completed_at = null
             where id = (select id from public.processing_jobs
                          where learner_id = %s and job_type = 'PROCESS_RAW_MESSAGE'
                            and state = 'COMPLETED' order by created_at limit 1)
            returning id
            """,
            (
                f"RuntimeError: simulated failure (key={secret}, user=p7-owner@example.test)",
                learner,
            ),
        ).fetchone()
    return str(job)


def synthetic_candidate(pool, label: str, course: str) -> str:
    name = f"SkillMirror P7 acceptance candidate {label} {uuid.uuid4().hex[:8]}"
    with pool.connection() as conn:
        (candidate,) = conn.execute(
            """
            insert into public.skill_candidates (canonical_name, normalized_name, description,
                                                 first_course_id)
            values (%s, %s, 'A synthetic candidate for the P7 acceptance (rejected).', %s)
            returning id
            """,
            (name, skill_key(name), course),
        ).fetchone()
    return str(candidate)


def prepare(args, pool, supabase: Http) -> Report:
    report = Report()
    local = args.local_graph
    domain = "example.test" if local else "mailinator.com"
    state: dict = {"target": "local" if local else "hosted", "api": args.api}

    if local:
        seeder = sign_up(supabase, "source", domain)
        with pool.connection() as conn, conn.transaction():
            source, skills = seed_graph(conn, uuid.UUID(seeder["id"]), "P7L ")
        source = str(source)
        roles = {k: str(skills[k]) for k in ROLES}
        state["seeder"] = seeder
        state["local_graph_nodes"] = [str(v) for v in skills.values()]
    else:
        source = args.course
        roles = {
            key: str(
                one(
                    pool,
                    "select n.id from public.skill_nodes n join public.course_skills cs "
                    "on cs.skill_id = n.id and cs.course_id = %s "
                    "where n.canonical_name = %s and n.status = 'ACTIVE'",
                    source,
                    name,
                )[0]
            )
            for key, name in ROLE_NAMES.items()
        }
    real = str(one(pool, "select owner_id from public.courses where id = %s", source)[0])
    state.update(
        source_course=source,
        roles=roles,
        real_learner_id=real,
        real_snapshot=real_snapshot(pool, real),
        model_runs=model_runs(pool),
        registry=registry_counts(pool),
        open_jobs=one(
            pool,
            "select count(*) from public.processing_jobs where state in ('PENDING', 'RETRY_WAIT', 'PROCESSING')",
        )[0],
    )
    report.check(
        "P1",
        "source course has the five role skills (prerequisite variables -> indexing)",
        one(
            pool,
            "select count(*) from public.skill_edges where edge_type = 'PREREQUISITE' "
            "and from_skill_id = %s and to_skill_id = %s",
            roles["variables"],
            roles["indexing"],
        )[0]
        == 1,
        {"source": source, "roles": len(roles)},
    )

    accounts = {label: sign_up(supabase, label, domain) for label in ACCOUNTS}
    state["accounts"] = accounts
    save(state)
    with pool.connection() as conn:
        for label, role in (("teacher", "TEACHER"), ("outsider", "TEACHER"), ("admin", "ADMIN")):
            change_role(conn, uuid.UUID(accounts[label]["id"]), role)
    report.check(
        "P2",
        "operator role grants (audited ROLE_CHANGE)",
        [
            r[0]
            for r in rows(
                pool,
                "select p.role::text from public.profiles p where p.id = any(%s::uuid[]) "
                "order by array_position(%s::uuid[], p.id)",
                [accounts[k]["id"] for k in ACCOUNTS],
                [accounts[k]["id"] for k in ACCOUNTS],
            )
        ]
        == ["STUDENT", "STUDENT", "STUDENT", "TEACHER", "TEACHER", "ADMIN"],
        "3 students, 2 teachers, 1 admin",
    )

    students = [accounts[k]["id"] for k in ("student1", "student2", "student3")]
    course = disposable_course(pool, students[0], "SkillMirror P7 acceptance class", roles, source)
    small = disposable_course(
        pool, students[0], "SkillMirror P7 acceptance small group", roles, source
    )
    for student in students:
        seed_p5_learner_on_course(
            pool, uuid.UUID(student), uuid.UUID(course), {k: uuid.UUID(v) for k, v in roles.items()}
        )
    state.update(course=course, small_course=small)
    save(state)

    time.sleep(3)  # tolerate a trailing local clock (the backend also allows 5 s of skew)
    admin = token(supabase, accounts["admin"])
    enroll = []
    for course_id, label, role in (
        (course, "teacher", "TEACHER"),
        (small, "student1", "STUDENT"),
        (small, "student2", "STUDENT"),
        (small, "teacher", "TEACHER"),
    ):
        key = f"p7-enroll-{uuid.uuid4().hex}"
        status, body = call(
            args.api,
            admin,
            "POST",
            f"/v1/admin/courses/{course_id}/members",
            {"email": accounts[label]["email"], "role": role},
            key,
        )
        enroll.append(
            {"course": course_id, "label": label, "role": role, "key": key, "status": status}
        )
    state["enrollments"] = enroll
    report.check(
        "P3",
        "memberships through POST /v1/admin/courses/{id}/members",
        [e["status"] for e in enroll] == [201, 201, 201, 201],
        [e["status"] for e in enroll],
    )

    state["jobs"] = {"browser": failed_job(pool, students[0]), "api": failed_job(pool, students[1])}
    state["candidates"] = {
        "browser": synthetic_candidate(pool, "browser", course),
        "api": synthetic_candidate(pool, "api", course),
    }
    save(state)
    report.check(
        "P4",
        "two FAILED jobs and two PENDING synthetic candidates",
        one(
            pool,
            "select (select count(*) from public.processing_jobs where id = any(%s::uuid[]) and state = 'FAILED'), "
            "(select count(*) from public.skill_candidates where id = any(%s::uuid[]) and status = 'PENDING_REVIEW')",
            list(state["jobs"].values()),
            list(state["candidates"].values()),
        )
        == (2, 2),
        {"jobs": state["jobs"], "candidates": state["candidates"]},
    )

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ui.env").write_text(
        "\n".join(
            [
                f"P7_TEACHER_EMAIL={accounts['teacher']['email']}",
                f"P7_TEACHER_PASSWORD={accounts['teacher']['password']}",
                f"P7_ADMIN_EMAIL={accounts['admin']['email']}",
                f"P7_ADMIN_PASSWORD={accounts['admin']['password']}",
                f"P7_STUDENT_EMAIL={accounts['student3']['email']}",
                f"P7_STUDENT_PASSWORD={accounts['student3']['password']}",
                f"P7_ACCEPTANCE_EXPECT={OUT / 'ui-expect.json'}",
                f"P7_ACCEPTANCE_OUT={OUT}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (OUT / "ui-expect.json").write_text(
        json.dumps(
            {
                "course_id": course,
                "small_course_id": small,
                "job_id": state["jobs"]["browser"],
                "candidate_id": state["candidates"]["browser"],
                "skills": 5,
                "students": 3,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    state["prepare"] = report.results
    save(state)
    return report


# --- verify ------------------------------------------------------------------------------------


def verify(args, pool, supabase: Http) -> Report:
    report = Report()
    state = load()
    accounts, course, small = state["accounts"], state["course"], state["small_course"]
    tokens = {label: token(supabase, accounts[label]) for label in ACCOUNTS}
    time.sleep(3)
    api = args.api

    def get(label, path):
        return call(api, tokens[label] if label else None, "GET", path)

    # V1 roles come from the database.
    roles = {label: get(label, "/v1/me")[1]["role"] for label in ACCOUNTS}
    report.check(
        "V1",
        "GET /v1/me: the profile role for every account",
        roles
        == {
            "student1": "STUDENT",
            "student2": "STUDENT",
            "student3": "STUDENT",
            "teacher": "TEACHER",
            "outsider": "TEACHER",
            "admin": "ADMIN",
        },
        roles,
    )

    # V2 a forged (client-writable) user_metadata role changes nothing.
    status, _ = Http(supabase.base, tokens["student3"], supabase.apikey).call(
        "PUT", "/auth/v1/user", {"data": {"role": "ADMIN"}}
    )
    forged = token(supabase, accounts["student3"])
    time.sleep(3)
    me = call(api, forged, "GET", "/v1/me")[1]
    blocked = [
        call(api, forged, m, p)[0]
        for m, p in (("GET", "/v1/admin/overview"), ("GET", "/v1/teacher/courses"))
    ]
    report.check(
        "V2",
        "user_metadata {role: ADMIN} (set by the client itself) grants nothing",
        status == 200 and me["role"] == "STUDENT" and blocked == [403, 403],
        {"update": status, "me": me["role"], "admin/teacher": blocked},
    )

    # V3 / V4 the authorization matrix.
    routes = teacher_routes(course)
    anonymous = {p: call(api, None, m, p)[0] for m, p in [("GET", "/v1/me"), *routes]}
    report.check(
        "V3", "anonymous: 401 on every P7 route", set(anonymous.values()) == {401}, len(anonymous)
    )
    matrix = {}
    for label in ("student1", "teacher", "outsider", "admin"):
        matrix[label] = [call(api, tokens[label], m, p)[0] for m, p in routes]
    expected_teacher = [200, 200] + [403] * 14
    expected_outsider = [200, 404] + [403] * 14
    admin_ok = all(s in (200, 404, 422) for s in matrix["admin"][2:]) and matrix["admin"][:2] == [
        200,
        200,
    ]
    report.check(
        "V4",
        "matrix: student 403 / teacher own course 200, admin 403 / outsider teacher 404 / admin 200",
        matrix["student1"] == [403] * 16
        and matrix["teacher"] == expected_teacher
        and matrix["outsider"] == expected_outsider
        and admin_ok,
        matrix,
    )

    # V5 teacher course list.
    listed = get("teacher", "/v1/teacher/courses")[1]
    by_id = {c["id"]: c for c in listed["courses"]}
    report.check(
        "V5",
        "teacher list: the class (3 students) and the small group (2, suppressed)",
        listed["min_cohort"] == 3
        and set(by_id) == {course, small}
        and (by_id[course]["student_count"], by_id[course]["suppressed"]) == (3, False)
        and (by_id[small]["student_count"], by_id[small]["suppressed"]) == (2, True),
        {k: (v["student_count"], v["suppressed"]) for k, v in by_id.items()},
    )

    # V6 / V7 exact aggregates and privacy.
    status, overview = get("teacher", f"/v1/teacher/courses/{course}/overview")
    skills = {row["skill_id"]: row for row in overview["skills"]}
    per_skill = {key: skills[state["roles"][key]]["states"] for key in EXPECTED_STATES}
    exact = all(
        states[EXPECTED_STATES[key]] == 3 and sum(states.values()) == 3
        for key, states in per_skill.items()
    )
    needs = {n["skill_id"]: n["students"] for n in overview["verification_needs"]}
    mapped = {m["skill_id"]: m["students"] for m in overview["common_mapped_skills"]}
    report.check(
        "V6",
        "overview: exact per-skill states, UNKNOWN its own count, evidence, needs, mapped skills",
        status == 200
        and exact
        and overview["state_totals"]
        == {
            "UNKNOWN": 6,
            "EMERGING": 3,
            "DEVELOPING": 3,
            "DEMONSTRATED": 3,
            "VERIFIED": 0,
            "NEEDS_REVERIFICATION": 0,
        }
        and overview["evidence_counts"]["independent"] == 27
        and overview["evidence_counts"]["students_with_evidence"] == 3
        and needs == {state["roles"]["comprehensions"]: 3}
        and mapped
        == {state["roles"][k]: 3 for k in ("for_loops", "comprehensions", "variables", "indexing")},
        {
            "totals": overview["state_totals"],
            "evidence": overview["evidence_counts"],
            "needs": len(needs),
            "mapped": len(mapped),
        },
    )
    text = json.dumps(overview)
    leaked = [
        label
        for label in ACCOUNTS
        if accounts[label]["id"] in text or accounts[label]["email"] in text
    ]
    report.check(
        "V7",
        "no learner id, e-mail, debt, actor or AI-usage field in the overview",
        not leaked and not keys_in(overview) & FORBIDDEN_KEYS,
        {"leaked": leaked, "forbidden_keys": sorted(keys_in(overview) & FORBIDDEN_KEYS)},
    )

    # V8 suppression.
    small_view = get("teacher", f"/v1/teacher/courses/{small}/overview")[1]
    report.check(
        "V8",
        "below the minimum cohort: cohort size only",
        small_view["cohort"] == {"student_count": 2, "min_cohort": 3, "suppressed": True}
        and small_view["state_totals"] is None
        and small_view["skills"] == []
        and small_view["evidence_counts"] is None,
        small_view["cohort"],
    )

    # V9 N2: a teacher membership is never a learning context.
    teacher_courses = get("teacher", "/v1/courses")[1]["courses"]
    teacher_ledger = get("teacher", "/v1/ledger")[1]["skills"]
    skill_status = get("teacher", f"/v1/skills/{state['roles']['for_loops']}")[0]
    rec_status = get("teacher", f"/v1/recommendations?course_id={course}")[0]
    student_courses = {c["id"] for c in get("student1", "/v1/courses")[1]["courses"]}
    report.check(
        "V9",
        "N2: the taught courses are not the teacher's learning context",
        teacher_courses == []
        and teacher_ledger == []
        and skill_status == 404
        and rec_status == 404
        and {course, small} <= student_courses,
        {
            "courses": len(teacher_courses),
            "ledger": len(teacher_ledger),
            "skill": skill_status,
            "recs": rec_status,
        },
    )

    # V10 admin reads.
    overview_admin = get("admin", "/v1/admin/overview")[1]
    failed = get("admin", "/v1/admin/jobs?state=FAILED&limit=200")[1]
    api_job = next((j for j in failed["jobs"] if j["id"] == state["jobs"]["api"]), None)
    runs_status, runs = get("admin", "/v1/admin/model-runs?limit=50")
    lookup = get("admin", f"/v1/admin/courses?q={course}")[1]["courses"]
    candidates = get("admin", "/v1/admin/skill-candidates")[1]
    report.check(
        "V10",
        "admin reads: overview, failed job with a redacted error, model runs without output, lookup, queue",
        api_job is not None
        and api_job["retry_mode"] == "RETRY"
        and "AIza" not in (api_job["last_error"] or "")
        and "@example.test" not in (api_job["last_error"] or "")
        and runs_status == 200
        and "output" not in keys_in(runs)
        and [(c["student_count"], c["teacher_count"]) for c in lookup] == [(3, 1)]
        and state["candidates"]["api"] in {c["id"] for c in candidates["candidates"]}
        and "budget" in overview_admin,
        {
            "retryable_failed": overview_admin["retryable_failed"],
            "error": (api_job or {}).get("last_error"),
            "lookup": lookup and lookup[0]["name"],
        },
    )

    # V11 retry over the API: once per key, audited.
    job = state["jobs"]["api"]
    key = f"p7-retry-{uuid.uuid4().hex}"
    first = call(
        api, tokens["admin"], "POST", f"/v1/admin/jobs/{job}/retry", {"mode": "RETRY"}, key
    )
    replay = call(
        api, tokens["admin"], "POST", f"/v1/admin/jobs/{job}/retry", {"mode": "RETRY"}, key
    )
    other = call(
        api,
        tokens["admin"],
        "POST",
        f"/v1/admin/jobs/{job}/retry",
        {"mode": "RESUME_ATTRIBUTION"},
        key,
    )
    again = call(api, tokens["admin"], "POST", f"/v1/admin/jobs/{job}/retry", {"mode": "RETRY"})
    audit = rows(
        pool,
        "select action::text, actor_role::text from public.audit_events where entity_id = %s",
        job,
    )
    report.check(
        "V11",
        "retry: FAILED -> PENDING once per key (replay 200, other body 409, not failed 409), audited",
        first[0] == 200
        and first[1]["job"]["state"] == "PENDING"
        and first[1]["job"]["manual_retry_count"] == 1
        and replay[0] == 200
        and replay[1]["replayed"] is True
        and other[0] == 409
        and again[0] == 409
        and audit == [("JOB_RETRY", "ADMIN")],
        {
            "first": first[0],
            "replay": replay[0],
            "other": other[0],
            "again": again[0],
            "audit": audit,
        },
    )

    # V12 the browser's retry and reject.
    browser_job = one(
        pool,
        "select state::text, manual_retry_count from public.processing_jobs where id = %s",
        state["jobs"]["browser"],
    )
    browser_candidate = one(
        pool,
        "select status::text from public.skill_candidates where id = %s",
        state["candidates"]["browser"],
    )
    browser_audit = rows(
        pool,
        "select action::text from public.audit_events where entity_id = any(%s::uuid[]) order by action",
        [state["jobs"]["browser"], state["candidates"]["browser"]],
    )
    report.check(
        "V12",
        "the walkthrough's retry and reject happened once, audited",
        tuple(browser_job) == ("PENDING", 1)
        and browser_candidate == ("REJECTED",)
        and [a[0] for a in browser_audit] == ["CANDIDATE_REJECT", "JOB_RETRY"],
        {"job": browser_job, "candidate": browser_candidate, "audit": browser_audit},
    )

    # V13 candidate REJECT over the API.
    candidate = state["candidates"]["api"]
    key = f"p7-reject-{uuid.uuid4().hex}"
    body = {"action": "REJECT", "note": "Synthetic acceptance candidate."}
    rejected = call(
        api, tokens["admin"], "POST", f"/v1/admin/skill-candidates/{candidate}/review", body, key
    )
    replayed = call(
        api, tokens["admin"], "POST", f"/v1/admin/skill-candidates/{candidate}/review", body, key
    )
    twice = call(
        api, tokens["admin"], "POST", f"/v1/admin/skill-candidates/{candidate}/review", body
    )
    report.check(
        "V13",
        "candidate review: REJECT once per key, a second review is 409",
        rejected[0] == 200
        and rejected[1]["candidate"]["status"] == "REJECTED"
        and replayed[1]["replayed"] is True
        and twice[0] == 409,
        {"reject": rejected[0], "replay": replayed[0], "twice": twice[0]},
    )

    # V14 enrollment: replay and the teacher-role guard.
    first_enroll = state["enrollments"][0]
    replay_enroll = call(
        api,
        tokens["admin"],
        "POST",
        f"/v1/admin/courses/{first_enroll['course']}/members",
        {"email": accounts["teacher"]["email"], "role": "TEACHER"},
        first_enroll["key"],
    )
    guarded = call(
        api,
        tokens["admin"],
        "POST",
        f"/v1/admin/courses/{small}/members",
        {"email": accounts["student3"]["email"], "role": "TEACHER"},
    )
    report.check(
        "V14",
        "enrollment: replay 200, a STUDENT profile cannot be a TEACHER member (422)",
        replay_enroll[0] == 200 and replay_enroll[1]["replayed"] is True and guarded[0] == 422,
        {"replay": replay_enroll[0], "guard": guarded[0]},
    )

    # V15 audit trail.
    actions = dict(
        rows(
            pool,
            "select action::text, count(*) from public.audit_events "
            "where entity_id = any(%s::uuid[]) group by 1",
            [
                *[accounts[k]["id"] for k in ("teacher", "outsider", "admin")],
                course,
                small,
                *state["jobs"].values(),
                *state["candidates"].values(),
            ],
        )
    )
    report.check(
        "V15",
        "audit: 3 ROLE_CHANGE (operator), 4 COURSE_MEMBER_ADD, 2 JOB_RETRY, 2 CANDIDATE_REJECT",
        actions
        == {"ROLE_CHANGE": 3, "COURSE_MEMBER_ADD": 4, "JOB_RETRY": 2, "CANDIDATE_REJECT": 2},
        actions,
    )

    # V16 zero model calls; registry and real learner unchanged.
    report.check(
        "V16",
        "no model run belongs to the run's accounts, courses or jobs; the real learner is unchanged",
        attributable_runs(pool, state) == 0
        and real_snapshot(pool, state["real_learner_id"]) == state["real_snapshot"],
        {"attributable_runs": attributable_runs(pool, state)},
    )
    unchanged = (
        model_runs(pool) == state["model_runs"] and registry_counts(pool) == state["registry"]
    )
    detail = {"model_runs": model_runs(pool), "registry": registry_counts(pool)}
    if state["target"] == "hosted":
        report.check("V17", "global model_runs and registry counts unchanged", unchanged, detail)
    else:
        # The local stack is shared with other local work (tests); informational only.
        print(f"[INFO] V17 local global counts {'unchanged' if unchanged else 'changed'}: {detail}")
    state["verify"] = report.results
    save(state)
    return report


# --- cleanup -----------------------------------------------------------------------------------


def cleanup(args, pool, supabase: Http) -> Report:
    import urllib.request

    report = Report()
    state = load()
    service_key = args.service_key or env_value(
        REPO / "apps" / "web" / ".env.e2e.local", "SUPABASE_SERVICE_ROLE_KEY"
    )
    runs_of_the_run = attributable_runs(pool, state)  # before the deletes null the references
    with pool.connection() as conn:
        conn.execute(
            "delete from public.skill_candidates where id = any(%s::uuid[])",
            (list(state.get("candidates", {}).values()),),
        )
    users = [state["accounts"][k]["id"] for k in ACCOUNTS if k in state.get("accounts", {})]
    if state.get("seeder"):
        users.append(state["seeder"]["id"])
    statuses = []
    for user in users:
        request = urllib.request.Request(  # noqa: S310 - the project's own Auth admin endpoint
            f"{supabase.base}/auth/v1/admin/users/{user}",
            method="DELETE",
            headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            statuses.append(response.status)
    remaining = {u: learner_rows(pool, u) for u in users}
    left = {u: {t: n for t, n in c.items() if n} for u, c in remaining.items() if any(c.values())}
    auth_left = one(pool, "select count(*) from auth.users where id = any(%s::uuid[])", users)[0]
    report.check(
        "C1",
        "accounts deleted through the Auth cascade; nothing learner-owned remains",
        set(statuses) == {200} and auth_left == 0 and not left,
        {"deleted": len(statuses), "auth_left": auth_left, "left": left},
    )
    candidates_left = one(
        pool,
        "select count(*) from public.skill_candidates where id = any(%s::uuid[])",
        list(state.get("candidates", {}).values()),
    )[0]
    courses_left = one(
        pool,
        "select count(*) from public.courses where id = any(%s::uuid[])",
        [c for c in (state.get("course"), state.get("small_course")) if c],
    )[0]
    report.check(
        "C2",
        "synthetic candidates and the disposable courses are gone",
        candidates_left == 0 and courses_left == 0,
        {"candidates": candidates_left, "courses": courses_left},
    )
    if state["target"] == "hosted":
        report.check(
            "C3",
            "registry unchanged, 0 model requests, real learner unchanged",
            registry_counts(pool) == state["registry"]
            and model_runs(pool) == state["model_runs"]
            and real_snapshot(pool, state["real_learner_id"]) == state["real_snapshot"],
            {"registry": registry_counts(pool), "model_runs": model_runs(pool)},
        )
    else:
        # The local rehearsal's own registry rows (the seeded P7L graph), like the test fixture's
        # teardown: the local stack may be shared with other work, so no `db reset` is needed.
        with pool.connection() as conn, conn.transaction():
            ids = [
                r[0]
                for r in conn.execute(
                    "select id from public.skill_nodes where id = any(%s::uuid[]) "
                    "or canonical_name like 'P7L %%'",
                    (list(state.get("roles", {}).values()) + state.get("local_graph_nodes", []),),
                ).fetchall()
            ]
            conn.execute(
                "delete from public.skill_edges where from_skill_id = any(%s) or to_skill_id = any(%s)",
                (ids, ids),
            )
            conn.execute("delete from public.skill_aliases where skill_id = any(%s)", (ids,))
            conn.execute("delete from public.skill_nodes where id = any(%s)", (ids,))
        report.check(
            "C3",
            "no model run belonged to the run; the local P7L graph removed",
            runs_of_the_run == 0
            and one(
                pool, "select count(*) from public.skill_nodes where canonical_name like 'P7L %%'"
            )[0]
            == 0,
            {"attributable_runs": runs_of_the_run, "graph_nodes_removed": len(ids)},
        )
    for name in ("ui.env",):
        (OUT / name).unlink(missing_ok=True)
    state["cleanup"] = report.results
    save(state)
    return report


PHASES = {"prepare": prepare, "verify": verify, "cleanup": cleanup}


def main() -> int:
    global OUT
    parser = argparse.ArgumentParser(description="SkillMirror P7 acceptance")
    parser.add_argument("phase", choices=tuple(PHASES))
    parser.add_argument("--api", default="http://127.0.0.1:8001")
    parser.add_argument("--course", default=HOSTED_COURSE)
    parser.add_argument("--local-graph", action="store_true", help="local rehearsal")
    parser.add_argument("--anon-key", default=None)
    parser.add_argument("--service-key", default=None)
    args = parser.parse_args()
    settings = get_settings()
    supabase_url = str(settings.supabase_url).rstrip("/")
    OUT = REPO / "test-results" / ("p7-local" if args.local_graph else "p7-hosted")
    # The target is explicit on every phase, and must match where the settings point.
    local_url = any(h in supabase_url for h in ("127.0.0.1", "localhost"))
    if local_url != args.local_graph:
        raise SystemExit(
            f"refusing: --local-graph={args.local_graph} but SUPABASE_URL is {supabase_url}"
        )
    print(f"target: {supabase_url} ({'local' if args.local_graph else 'hosted'})")
    anon = args.anon_key or env_value(
        REPO / "apps" / "web" / ".env.local", "NEXT_PUBLIC_SUPABASE_ANON_KEY"
    )
    pool = create_pool(settings.database_url.get_secret_value(), max_size=4)
    try:
        report = PHASES[args.phase](args, pool, Http(supabase_url, apikey=anon))
    finally:
        pool.close()
    (OUT / f"evidence-{args.phase}.json").write_text(
        json.dumps({"passed": report.passed, "results": report.results}, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"P7 {args.phase.upper()}:", "PASS" if report.passed else "FAIL")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
