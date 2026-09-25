"""P7 acceptance: the teacher overview and the admin operations over real HTTP (manual gate; never
run in CI). No model call at all.

Targets
    hosted (default)  only after migration 0009 is approved, pushed and verified on hosted. The
                      class is the EXISTING course 9440004a: the fixture students join it with
                      learner-owned STUDENT memberships and seed learner-owned evidence on five of
                      its canonical skills. No skill_nodes, aliases, edges, course_skills or
                      embeddings are created. The real P3B/P4 learner (the course's owner) is only
                      read - aggregated by the teacher overview, hashed before and after.
    --local-graph     a local rehearsal (give it on EVERY phase): the class is a P5 fixture graph
                      seeded for a throw-away local account; cleanup removes its prefixed registry
                      rows. The script refuses a target that does not match SUPABASE_URL.

The class already has members, so its expected aggregates are its baseline (read before the
fixture students join, in a READ ONLY transaction) plus the fixture's exact contribution. The
small group (suppression) is a disposable course with no skills.

Every account is disposable (Auth signup; @mailinator.com on hosted) and deleted at cleanup
through the Auth admin API (the normal cascade removes its memberships, the small course and
every learner-owned row). Two synthetic skill_candidates, named "ACCEPTANCE TEST ...", exercise
REJECT (never APPROVE / MERGE) and are deleted at cleanup. Audit events are kept as the record of
the admin actions (their actor ids become null when the accounts are deleted).

    prepare  snapshot; sign up 3 students, a teacher, an outsider teacher and an admin; operator
             role grants (grant_role.change_role, audited); the teacher enrolled in the class
             through POST /v1/admin/courses/{id}/members; the class baseline; the 3 students join
             the class with P5 evidence (production code, no model call); the small group (2
             students + the teacher, through the API); two FAILED jobs and two synthetic PENDING
             candidates (one each for the browser, one each for verify); walkthrough files
    (then)   apps/web/e2e/p7-acceptance.spec.ts: teacher pages, admin pages (retry + reject in the
             browser), a student's forbidden panel
    verify   roles from the database, forged metadata, the authorization matrix, exact cohort
             aggregates, suppression, privacy, N2, admin reads, retry / review / enrollment
             idempotency, audit rows, zero model calls of the run, registry and real learner
    cleanup  delete the candidates and the accounts; prove nothing of the fixture remains

    cd services/backend
    # API: services/backend/.env with GEMINI_API_KEY blank and WORKER_ENABLED=false (port 8001)
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

import psycopg  # noqa: E402
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

from app.auth.roles import Actor  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db.pool import create_pool  # noqa: E402
from app.intelligence.skill_graph.canonical import skill_key  # noqa: E402
from app.teacher.models import TeacherViewPolicy  # noqa: E402
from app.teacher.service import course_overview  # noqa: E402
from tests.p5_fixtures import ROLES, seed_graph, seed_p5_learner_on_course  # noqa: E402

HOSTED_COURSE = "9440004a-a25e-4e15-94c0-17c21f6bd695"
ACCOUNTS = ("student1", "student2", "student3", "teacher", "outsider", "admin")
STATES = ("UNKNOWN", "EMERGING", "DEVELOPING", "DEMONSTRATED", "VERIFIED", "NEEDS_REVERIFICATION")
# What the P5 fixture makes of each role skill for every fixture student (tests/p5_fixtures.py).
EXPECTED_STATES = {
    "for_loops": "DEMONSTRATED",
    "while_loops": "UNKNOWN",
    "comprehensions": "UNKNOWN",
    "variables": "EMERGING",
    "indexing": "DEVELOPING",
}
FIXTURE_STUDENTS = 3
FIXTURE_INDEPENDENT = 9  # per student: 4 for_loops + 2 variables + 3 indexing (one excluded)
FIXTURE_MAPPED = ("for_loops", "comprehensions", "variables", "indexing")
CANDIDATE_PREFIX = "ACCEPTANCE TEST P7 synthetic candidate"
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


def registry_counts(pool) -> dict[str, int]:
    """The shared rows the acceptance must never create."""
    names = ("skill_nodes", "skill_edges", "skill_aliases", "course_skills", "skill_embeddings")
    values = one(
        pool,
        "select " + ", ".join(f"(select count(*) from public.{t})" for t in names),  # noqa: S608
    )
    return dict(zip(names, values, strict=True))


def class_members(pool, course: str) -> list[list[str]]:
    return [
        [str(r[0]), r[1]]
        for r in rows(
            pool,
            "select user_id, role::text from public.course_memberships where course_id = %s "
            "order by user_id",
            course,
        )
    ]


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
    """Model runs of the acceptance's accounts, its small course or its jobs (must stay 0)."""
    return one(
        pool,
        "select count(*) from public.model_runs where learner_id = any(%s::uuid[]) "
        "or course_id = any(%s::uuid[]) or processing_job_id = any(%s::uuid[])",
        [a["id"] for a in state.get("accounts", {}).values()],
        [c for c in (state.get("small_course"),) if c],
        list(state.get("jobs", {}).values()),
    )[0]


def fixture_candidates_left(pool, state: dict) -> int:
    return one(
        pool,
        "select count(*) from public.skill_candidates where id = any(%s::uuid[]) "
        "or canonical_name like %s",
        list(state.get("candidates", {}).values()),
        f"{CANDIDATE_PREFIX}%",
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


# --- expectations ------------------------------------------------------------------------------


def baseline(pool, teacher: str, course: str, exclude: list[str] | None = None) -> dict:
    """The class as the overview counts it WITHOUT the fixture students (the cohort floor is
    bypassed here only, so a small existing cohort is still counted).

    Before they join: a READ ONLY transaction. Afterwards (`exclude`): their STUDENT memberships
    are removed inside a transaction that is ALWAYS rolled back, so the class's other members are
    counted exactly as they are now, even while other activity changes them."""
    policy = TeacherViewPolicy.model_construct(min_cohort=1, window_days=30, top_n=10_000)
    actor = Actor(uuid.UUID(teacher), None, "TEACHER")
    with pool.connection() as conn:
        with conn.transaction():
            if exclude:
                conn.execute(
                    "delete from public.course_memberships where course_id = %s "
                    "and user_id = any(%s::uuid[]) and role = 'STUDENT'",
                    (course, exclude),
                )
            else:
                conn.execute("set transaction read only")
            view = course_overview(conn, actor, uuid.UUID(course), policy)
            if exclude:
                raise psycopg.Rollback()
    if view.cohort.suppressed:  # no existing student: every count is 0
        empty = dict.fromkeys(STATES, 0)
        skill_ids = [
            str(r[0])
            for r in rows(
                pool,
                "select cs.skill_id from public.course_skills cs join public.skill_nodes n "
                "on n.id = cs.skill_id where cs.course_id = %s and cs.active "
                "and n.status = 'ACTIVE' and n.node_kind in ('SKILL', 'SUBSKILL')",
                course,
            )
        ]
        return {
            "students": 0,
            "states": {sid: dict(empty) for sid in skill_ids},
            "with_evidence": {},
            "needs": {},
            "mapped": {},
            "independent": 0,
            "verification": 0,
            "students_with_evidence": 0,
            "totals": empty,
            "skills": view.course.skill_count,
        }
    return {
        "students": view.cohort.student_count,
        "states": {str(r.skill_id): dict(r.states) for r in view.skills},
        "with_evidence": {str(r.skill_id): r.students_with_evidence for r in view.skills},
        "needs": {str(n.skill_id): n.students for n in view.verification_needs},
        "mapped": {str(m.skill_id): m.students for m in view.common_mapped_skills},
        "independent": view.evidence_counts.independent,
        "verification": view.evidence_counts.verification,
        "students_with_evidence": view.evidence_counts.students_with_evidence,
        "totals": dict(view.state_totals),
        "skills": view.course.skill_count,
    }


def expected(state: dict) -> dict:
    """Baseline + the fixture: every fixture student is UNKNOWN on the class's other skills."""
    base, roles = state["baseline"], state["roles"]
    by_role = {sid: key for key, sid in roles.items()}
    states = {}
    for sid, counts in base["states"].items():
        states[sid] = dict(counts)
        fixture_state = EXPECTED_STATES[by_role[sid]] if sid in by_role else "UNKNOWN"
        states[sid][fixture_state] += FIXTURE_STUDENTS
    totals = {s: sum(c[s] for c in states.values()) for s in STATES}
    with_evidence = dict(base["with_evidence"])
    for key in ("for_loops", "variables", "indexing"):
        with_evidence[roles[key]] = with_evidence.get(roles[key], 0) + FIXTURE_STUDENTS
    needs = dict(base["needs"])
    needs[roles["comprehensions"]] = needs.get(roles["comprehensions"], 0) + FIXTURE_STUDENTS
    mapped = dict(base["mapped"])
    for key in FIXTURE_MAPPED:
        mapped[roles[key]] = mapped.get(roles[key], 0) + FIXTURE_STUDENTS
    return {
        "students": base["students"] + FIXTURE_STUDENTS,
        "states": states,
        "totals": totals,
        "with_evidence": with_evidence,
        "needs": needs,
        "mapped": mapped,
        "independent": base["independent"] + FIXTURE_STUDENTS * FIXTURE_INDEPENDENT,
        "verification": base["verification"],
        "students_with_evidence": base["students_with_evidence"] + FIXTURE_STUDENTS,
    }


# --- prepare -----------------------------------------------------------------------------------


def skill_free_course(pool, owner: str, name: str) -> str:
    """A learner-owned course with NO course_skills: enough for the cohort-size suppression."""
    with pool.connection() as conn:
        (course,) = conn.execute(
            """
            insert into public.courses (owner_id, name, subject, level, graph_status, graph_version)
            values (%s, %s, 'Python', 'Beginner', 'READY', 1) returning id
            """,
            (owner, name),
        ).fetchone()
    return str(course)


def failed_job(pool, learner: str) -> str:
    """One of the learner's seeded (completed, analysed and attributed) raw-message jobs, marked
    FAILED with an error carrying secret-looking text the admin API must redact. Re-running it
    makes 0 model requests: the turn is analysed and attributed already."""
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


def synthetic_candidate(pool, label: str) -> str:
    name = f"{CANDIDATE_PREFIX} {label} {uuid.uuid4().hex[:8]}"
    with pool.connection() as conn:
        (candidate,) = conn.execute(
            """
            insert into public.skill_candidates (canonical_name, normalized_name, description)
            values (%s, %s, 'ACCEPTANCE TEST fixture: rejected and deleted by the P7 acceptance.')
            returning id
            """,
            (name, skill_key(name)),
        ).fetchone()
    return str(candidate)


def enroll(api: str, admin: str, course: str, email: str, role: str) -> dict:
    key = f"p7-enroll-{uuid.uuid4().hex}"
    status, _ = call(
        api,
        admin,
        "POST",
        f"/v1/admin/courses/{course}/members",
        {"email": email, "role": role},
        key,
    )
    return {"course": course, "email": email, "role": role, "key": key, "status": status}


def prepare(args, pool, supabase: Http) -> Report:
    report = Report()
    local = args.local_graph
    domain = "example.test" if local else "mailinator.com"
    state: dict = {
        "target": "local" if local else "hosted",
        "api": args.api,
        "prepare_started": one(pool, "select now()")[0].isoformat(),
    }

    if local:
        seeder = sign_up(supabase, "source", domain)
        with pool.connection() as conn, conn.transaction():
            source, skills = seed_graph(conn, uuid.UUID(seeder["id"]), "P7L ")
        course = str(source)
        roles = {k: str(skills[k]) for k in ROLES}
        state["seeder"] = seeder
        state["local_graph_nodes"] = [str(v) for v in skills.values()]
    else:
        course = args.course
        roles = {
            key: str(
                one(
                    pool,
                    "select n.id from public.skill_nodes n join public.course_skills cs "
                    "on cs.skill_id = n.id and cs.course_id = %s "
                    "where n.canonical_name = %s and n.status = 'ACTIVE'",
                    course,
                    name,
                )[0]
            )
            for key, name in ROLE_NAMES.items()
        }
    real = str(one(pool, "select owner_id from public.courses where id = %s", course)[0])
    state.update(
        course=course,
        roles=roles,
        real_learner_id=real,
        real_snapshot=real_snapshot(pool, real),
        model_runs=model_runs(pool),
        registry=registry_counts(pool),
        class_members=class_members(pool, course),
    )
    report.check(
        "P1",
        "the class has the five role skills (prerequisite variables -> indexing)",
        one(
            pool,
            "select count(*) from public.skill_edges where edge_type = 'PREREQUISITE' "
            "and from_skill_id = %s and to_skill_id = %s",
            roles["variables"],
            roles["indexing"],
        )[0]
        == 1,
        {"class": course, "existing_members": len(state["class_members"])},
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

    time.sleep(3)  # tolerate a trailing local clock (the backend also allows 5 s of skew)
    admin = token(supabase, accounts["admin"])
    enrollments = [enroll(args.api, admin, course, accounts["teacher"]["email"], "TEACHER")]
    state["baseline"] = baseline(pool, accounts["teacher"]["id"], course)

    students = [accounts[k]["id"] for k in ("student1", "student2", "student3")]
    for student in students:
        seed_p5_learner_on_course(
            pool, uuid.UUID(student), uuid.UUID(course), {k: uuid.UUID(v) for k, v in roles.items()}
        )
    small = skill_free_course(pool, students[0], "ACCEPTANCE TEST P7 small group")
    state["small_course"] = small
    save(state)
    for label, role in (("student1", "STUDENT"), ("student2", "STUDENT"), ("teacher", "TEACHER")):
        enrollments.append(enroll(args.api, admin, small, accounts[label]["email"], role))
    state["enrollments"] = enrollments
    report.check(
        "P3",
        "memberships through POST /v1/admin/courses/{id}/members",
        [e["status"] for e in enrollments] == [201, 201, 201, 201],
        {
            "statuses": [e["status"] for e in enrollments],
            "baseline_students": state["baseline"]["students"],
        },
    )

    state["jobs"] = {"browser": failed_job(pool, students[0]), "api": failed_job(pool, students[1])}
    state["candidates"] = {
        "browser": synthetic_candidate(pool, "browser"),
        "api": synthetic_candidate(pool, "api"),
    }
    save(state)
    report.check(
        "P4",
        "two FAILED jobs and two ACCEPTANCE TEST candidates PENDING_REVIEW",
        one(
            pool,
            "select (select count(*) from public.processing_jobs where id = any(%s::uuid[]) "
            "and state = 'FAILED'), (select count(*) from public.skill_candidates "
            "where id = any(%s::uuid[]) and status = 'PENDING_REVIEW' and canonical_name like %s)",
            list(state["jobs"].values()),
            list(state["candidates"].values()),
            f"{CANDIDATE_PREFIX}%",
        )
        == (2, 2),
        {"jobs": state["jobs"], "candidates": state["candidates"]},
    )

    want = expected(state)
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
                "skills": state["baseline"]["skills"],
                "students": want["students"],
                "unknown_total": want["totals"]["UNKNOWN"],
                "independent": want["independent"],
                "comprehensions_needs": want["needs"][roles["comprehensions"]],
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
    want = expected(state)

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
    # The ADMIN is not a TEACHER member of the class: its teacher overview is 404 like any
    # non-member's, while the admin course lookup of the same course is 200.
    admin_ok = (
        all(s in (200, 404, 422) for s in matrix["admin"][2:])
        and matrix["admin"][:2] == [200, 404]
        and matrix["admin"][12] == 200
    )
    report.check(
        "V4",
        "matrix: student 403 / teacher own course 200, admin 403 / outsider teacher 404 / "
        "admin: teacher overview 404, admin routes 200",
        matrix["student1"] == [403] * 16
        and matrix["teacher"] == [200, 200] + [403] * 14
        and matrix["outsider"] == [200, 404] + [403] * 14
        and admin_ok,
        matrix,
    )

    # V5 teacher course list.
    listed = get("teacher", "/v1/teacher/courses")[1]
    by_id = {c["id"]: c for c in listed["courses"]}
    report.check(
        "V5",
        "teacher list: the class (baseline + 3 students) and the small group (2, suppressed)",
        listed["min_cohort"] == 3
        and set(by_id) == {course, small}
        and (by_id[course]["student_count"], by_id[course]["suppressed"])
        == (want["students"], False)
        and (by_id[small]["student_count"], by_id[small]["suppressed"]) == (2, True),
        {k: (v["student_count"], v["suppressed"]) for k, v in by_id.items()},
    )

    # V6 / V7 exact aggregates (baseline + fixture) and privacy. The baseline is re-read right
    # before the request (rolled back), so other members' activity since prepare is accounted for.
    fixture_students = [accounts[k]["id"] for k in ("student1", "student2", "student3")]
    fresh = baseline(pool, accounts["teacher"]["id"], course, exclude=fixture_students)
    drift = fresh != state["baseline"]
    want = expected({**state, "baseline": fresh})
    status, overview = get("teacher", f"/v1/teacher/courses/{course}/overview")
    skills = {row["skill_id"]: row for row in overview["skills"]}
    states_ok = {sid: row["states"] for sid, row in skills.items()} == want["states"]
    evidence_ok = all(
        row["students_with_evidence"] == want["with_evidence"].get(sid, 0)
        for sid, row in skills.items()
    )
    needs = {n["skill_id"]: n["students"] for n in overview["verification_needs"]}
    mapped = {m["skill_id"]: m["students"] for m in overview["common_mapped_skills"]}
    lists_ok = (
        all(n == want["needs"].get(sid) for sid, n in needs.items())
        and all(n == want["mapped"].get(sid) for sid, n in mapped.items())
        and state["roles"]["comprehensions"] in needs
    )
    counts = overview["evidence_counts"]
    report.check(
        "V6",
        "overview = baseline + fixture: per-skill states, UNKNOWN its own count, evidence, needs, mapped",
        status == 200
        and states_ok
        and evidence_ok
        and overview["state_totals"] == want["totals"]
        and all(sum(s.values()) == want["students"] for s in want["states"].values())
        and counts["independent"] == want["independent"]
        and counts["verification"] == want["verification"]
        and counts["students_with_evidence"] == want["students_with_evidence"]
        and lists_ok,
        {
            "baseline_students": fresh["students"],
            "baseline_changed_since_prepare": drift,
            "totals": overview["state_totals"],
            "evidence": counts,
            "needs": len(needs),
            "mapped": len(mapped),
        },
    )
    text = json.dumps(overview)
    people = {**{k: v["id"] for k, v in accounts.items()}, "real": state["real_learner_id"]}
    leaked = [k for k, v in people.items() if v in text] + [
        k for k, v in accounts.items() if v["email"] in text
    ]
    report.check(
        "V7",
        "no learner id (fixture or real), e-mail, debt, actor or AI-usage field in the overview",
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
    teachers = sum(1 for _, role in state["class_members"] if role == "TEACHER") + 1
    report.check(
        "V10",
        "admin reads: overview, failed job with a redacted error, model runs without output, "
        "lookup, queue",
        api_job is not None
        and api_job["retry_mode"] == "RETRY"
        and "AIza" not in (api_job["last_error"] or "")
        and "@example.test" not in (api_job["last_error"] or "")
        and runs_status == 200
        and "output" not in keys_in(runs)
        and [(c["student_count"], c["teacher_count"]) for c in lookup]
        == [(want["students"], teachers)]
        and state["candidates"]["api"] in {c["id"] for c in candidates["candidates"]}
        and "budget" in overview_admin,
        {
            "retryable_failed": overview_admin["retryable_failed"],
            "error": (api_job or {}).get("last_error"),
            "lookup": lookup and (lookup[0]["name"], lookup[0]["student_count"]),
        },
    )

    # V11 retry over the API: once per key, audited. (A worker attached elsewhere may run the job
    # at once: re-running the analysed, attributed turn is 0 model requests; V16 proves it.)
    job = state["jobs"]["api"]
    path = f"/v1/admin/jobs/{job}/retry"
    key = f"p7-retry-{uuid.uuid4().hex}"
    first = call(api, tokens["admin"], "POST", path, {"mode": "RETRY"}, key)
    replay = call(api, tokens["admin"], "POST", path, {"mode": "RETRY"}, key)
    other = call(api, tokens["admin"], "POST", path, {"mode": "RESUME_ATTRIBUTION"}, key)
    again = call(api, tokens["admin"], "POST", path, {"mode": "RETRY"})
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
        "select action::text from public.audit_events where entity_id = any(%s::uuid[]) "
        "order by action",
        [state["jobs"]["browser"], state["candidates"]["browser"]],
    )
    report.check(
        "V12",
        "the walkthrough's retry and reject happened once, audited",
        browser_job[0] in ("PENDING", "PROCESSING", "COMPLETED")
        and browser_job[1] == 1
        and browser_candidate == ("REJECTED",)
        and [a[0] for a in browser_audit] == ["CANDIDATE_REJECT", "JOB_RETRY"],
        {"job": browser_job, "candidate": browser_candidate, "audit": browser_audit},
    )

    # V13 candidate REJECT over the API (never APPROVE / MERGE).
    candidate = state["candidates"]["api"]
    path = f"/v1/admin/skill-candidates/{candidate}/review"
    key = f"p7-reject-{uuid.uuid4().hex}"
    body = {"action": "REJECT", "note": "ACCEPTANCE TEST candidate."}
    rejected = call(api, tokens["admin"], "POST", path, body, key)
    replayed = call(api, tokens["admin"], "POST", path, body, key)
    twice = call(api, tokens["admin"], "POST", path, body)
    report.check(
        "V13",
        "candidate review: REJECT once per key, a second review is 409",
        rejected[0] == 200
        and rejected[1]["candidate"]["status"] == "REJECTED"
        and rejected[1]["skill"] is None
        and rejected[1]["embed_job_id"] is None
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
        {"email": first_enroll["email"], "role": first_enroll["role"]},
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
            "where entity_id = any(%s::uuid[]) and created_at >= %s::timestamptz group by 1",
            [
                *[accounts[k]["id"] for k in ("teacher", "outsider", "admin")],
                course,
                small,
                *state["jobs"].values(),
                *state["candidates"].values(),
            ],
            state["prepare_started"],
        )
    )
    report.check(
        "V15",
        "audit: 3 ROLE_CHANGE (operator), 4 COURSE_MEMBER_ADD, 2 JOB_RETRY, 2 CANDIDATE_REJECT",
        actions
        == {"ROLE_CHANGE": 3, "COURSE_MEMBER_ADD": 4, "JOB_RETRY": 2, "CANDIDATE_REJECT": 2},
        actions,
    )

    # V16 zero model calls of the run; the real learner unchanged; no shared row created.
    report.check(
        "V16",
        "no model run belongs to the run's accounts, small course or jobs; real learner unchanged",
        attributable_runs(pool, state) == 0
        and real_snapshot(pool, state["real_learner_id"]) == state["real_snapshot"],
        {"attributable_runs": attributable_runs(pool, state)},
    )
    registry = registry_counts(pool)
    if state["target"] == "hosted":
        report.check(
            "V17",
            "no skill_nodes, edges, aliases, course_skills or embeddings created",
            registry == state["registry"],
            registry,
        )
    else:
        print(f"[INFO] V17 local shared counts (the local stack is shared): {registry}")
    now = model_runs(pool)
    print(f"[INFO] global model_runs {state['model_runs']} -> {now} (other activity may add runs)")
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
            "delete from public.skill_candidates where id = any(%s::uuid[]) "
            "and canonical_name like %s",
            (list(state.get("candidates", {}).values()), f"{CANDIDATE_PREFIX}%"),
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
    small_left = one(
        pool, "select count(*) from public.courses where id = %s", state.get("small_course")
    )[0]
    report.check(
        "C2",
        "0 ACCEPTANCE TEST candidate rows remain; the small course is gone",
        fixture_candidates_left(pool, state) == 0 and small_left == 0,
        {"candidates": fixture_candidates_left(pool, state), "small_course": small_left},
    )
    report.check(
        "C3",
        "no model run belonged to the run",
        runs_of_the_run == 0,
        {"attributable_runs": runs_of_the_run},
    )
    if state["target"] == "hosted":
        report.check(
            "C4",
            "the class has exactly its original members; no shared row was created",
            class_members(pool, state["course"]) == state["class_members"]
            and registry_counts(pool) == state["registry"],
            {"members": len(class_members(pool, state["course"])), "shared": registry_counts(pool)},
        )
        report.check(
            "C5",
            "the real P3B/P4 learner is unchanged",
            real_snapshot(pool, state["real_learner_id"]) == state["real_snapshot"],
            state["real_snapshot"][:16],
        )
    else:
        # The rehearsal's own registry rows (the seeded P7L graph), like the test fixtures'
        # teardown: the local stack may be shared with other work, so there is no `db reset`.
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
            "C4",
            "the local P7L graph removed",
            one(pool, "select count(*) from public.skill_nodes where canonical_name like 'P7L %%'")[
                0
            ]
            == 0,
            {"graph_nodes_removed": len(ids)},
        )
    (OUT / "ui.env").unlink(missing_ok=True)
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
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"evidence-{args.phase}.json").write_text(
        json.dumps({"passed": report.passed, "results": report.results}, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"P7 {args.phase.upper()}:", "PASS" if report.passed else "FAIL")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
