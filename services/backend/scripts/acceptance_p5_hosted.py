"""Hosted P5 acceptance on the existing course graph (manual gate; never run in CI).

One disposable learner, no model call, no registry row, and the real P3B/P4 learner's rows
untouched (hashed before and after). Phases:

    prepare  snapshot the real learner + model_runs; inspect the real learner's P5 view
             read-only (a rolled-back transaction); sign up a disposable learner through
             Supabase Auth; seed it on the existing course with the deterministic fixture;
             check 1-6 over HTTP; write the walkthrough credentials + expectations
    (then)   apps/web/e2e/p5-acceptance.spec.ts: the browser walkthrough, which applies
             DONT_COUNT (skill page) and WRONG_SKILL (activity page)
    verify   check 7-12 over HTTP and in the database
    cleanup  delete the disposable learner through the Auth admin API (the normal cascade)
             and prove no learner-owned row, registry change or model run remains

    cd services/backend
    # backend: services/backend/.env with GEMINI_API_KEY= (empty) and WORKER_ENABLED=false
    .venv/Scripts/python scripts/acceptance_p5_hosted.py prepare --api http://127.0.0.1:8001
    .venv/Scripts/python scripts/acceptance_p5_hosted.py verify  --api http://127.0.0.1:8001
    .venv/Scripts/python scripts/acceptance_p5_hosted.py cleanup

Connection values come from services/backend/.env (DATABASE_URL, SUPABASE_URL),
apps/web/.env.local (anon key) and apps/web/.env.e2e.local (service-role key, cleanup only);
none is printed or written. State, evidence and credentials go to test-results/p5-hosted/
(git-ignored).
"""

import argparse
import hashlib
import json
import secrets
import sys
import urllib.request
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from acceptance_p5 import (  # noqa: E402
    FORBIDDEN_WORDS,
    PROVENANCE,
    Http,
    ui_expectations,
    write_ui_files,
)

from app.core.config import get_settings  # noqa: E402
from app.db.pool import create_pool  # noqa: E402
from app.experience.activity import list_activity  # noqa: E402
from app.experience.skills import get_skill_detail  # noqa: E402
from app.intelligence.mastery.ledger import read_ledger  # noqa: E402
from app.intelligence.policy import load_policy  # noqa: E402
from tests.p5_fixtures import seed_p5_learner_on_course  # noqa: E402

COURSE = "9440004a-a25e-4e15-94c0-17c21f6bd695"
# Existing canonical skills of the course playing the fixture roles (no new skill_nodes).
ROLE_NAMES = {
    "for_loops": "Writing for loops over sequences",
    "while_loops": "Writing while loops",
    "comprehensions": "Using list comprehensions",
    "variables": "Assigning variables and data types",
    "indexing": "Creating and indexing lists",
}
OUT = REPO / "test-results" / "p5-hosted"
STATE = OUT / "state.json"
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
)


def env_value(path: Path, key: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit(f"{key} is not set in {path.relative_to(REPO)}")


def one(pool, sql: str, *params):
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchone()


def rows(pool, sql: str, *params):
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchall()


def model_runs(pool) -> dict[str, int]:
    kinds = dict(
        rows(
            pool,
            "select case when task_type like 'EMBED%%' then 'embedding' else 'generation' end, count(*) "
            "from public.model_runs group by 1",
        )
    )
    return {"generation": kinds.get("generation", 0), "embedding": kinds.get("embedding", 0)}


def real_snapshot(pool, learner: str) -> str:
    """A hash of every row the real P3B/P4 learner owns (evidence, provenance, ledger, P5 rows)."""
    digest = hashlib.sha256()
    for table in (
        *PROVENANCE,
        "skill_ledger",
        "conversations",
        "processing_jobs",
        "recommendations",
    ):
        data = rows(
            pool,
            f"select coalesce(json_agg(t order by t::text), '[]')::text from public.{table} t "  # noqa: S608
            "where t.learner_id = %s",
            learner,
        )[0][0]
        digest.update(f"{table}:{data}".encode())
    feedback = rows(pool, "select count(*) from public.feedback where user_id = %s", learner)[0][0]
    digest.update(f"feedback:{feedback}".encode())
    return digest.hexdigest()


def registry(pool) -> list[int]:
    return list(
        one(
            pool,
            "select (select count(*) from public.skill_nodes), (select count(*) from public.skill_edges), "
            "(select count(*) from public.skill_aliases), "
            "(select count(*) from public.course_skills where course_id = %s)",
            COURSE,
        )
    )


def learner_rows(pool, learner: str) -> dict[str, int]:
    counts = {
        t: one(pool, f"select count(*) from public.{t} where learner_id = %s", learner)[0]  # noqa: S608
        for t in LEARNER_TABLES
    }
    counts["feedback"] = one(
        pool, "select count(*) from public.feedback where user_id = %s", learner
    )[0]
    counts["course_memberships"] = one(
        pool, "select count(*) from public.course_memberships where user_id = %s", learner
    )[0]
    counts["profiles"] = one(pool, "select count(*) from public.profiles where id = %s", learner)[0]
    return counts


def provenance(pool, learner: str) -> dict[str, int]:
    return {
        t: one(pool, f"select count(*) from public.{t} where learner_id = %s", learner)[0]  # noqa: S608
        for t in PROVENANCE
    }


class Report:
    def __init__(self) -> None:
        self.results: list[dict] = []

    def check(self, number: str, name: str, ok: bool, detail: object) -> None:
        self.results.append({"check": number, "name": name, "passed": bool(ok), "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {number:>3} {name}: {detail}")

    @property
    def passed(self) -> bool:
        return all(r["passed"] for r in self.results)


def auth(supabase_url: str, anon: str) -> Http:
    return Http(supabase_url, apikey=anon)


def sign_in(supabase: Http, email: str, password: str) -> str:
    status, body = supabase.call(
        "POST", "/auth/v1/token?grant_type=password", {"email": email, "password": password}
    )
    assert status == 200, f"sign-in failed ({status})"
    return body["access_token"]


def save(state: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def prepare(args, pool, supabase_url: str, anon: str) -> Report:
    report = Report()
    owner = str(one(pool, "select owner_id from public.courses where id = %s", COURSE)[0])
    roles = {
        key: str(
            one(
                pool,
                "select id from public.skill_nodes where canonical_name = %s and status = 'ACTIVE'",
                name,
            )[0]
        )
        for key, name in ROLE_NAMES.items()
    }
    state = {
        "course_id": COURSE,
        "real_learner_id": owner,
        "roles": roles,
        "real_snapshot": real_snapshot(pool, owner),
        "model_runs": model_runs(pool),
        "registry": registry(pool),
        "open_jobs": one(
            pool,
            "select count(*) from public.processing_jobs where state not in ('COMPLETED', 'FAILED')",
        )[0],
    }

    # The real P3B/P4 learner's P5 view, read-only: everything runs in a rolled-back transaction.
    with pool.connection() as conn, conn.transaction(force_rollback=True):
        policy = load_policy(conn)
        ledger = read_ledger(
            conn, uuid.UUID(owner), policy=policy, course_id=uuid.UUID(COURSE)
        ).skills
        evidence_skill = conn.execute(
            "select skill_id from public.evidence_events where learner_id = %s", (owner,)
        ).fetchone()[0]
        detail = get_skill_detail(conn, uuid.UUID(owner), evidence_skill, policy=policy)
        activity = list_activity(conn, uuid.UUID(owner), limit=200)
    item = detail.evidence[0]
    chips = [m.evidence_id for r in activity.items for s in r.segments for m in s.mappings]
    report.check(
        "R",
        "real P3B/P4 learner: P5 view derived read-only from existing data",
        len(ledger) == 24
        and detail.mastery.state == "UNKNOWN"
        and detail.mastery.mastery_mean is None
        and detail.mastery.explanation_code == "NOT_ENOUGH_SUPPORT"
        and len(detail.evidence) == 1
        and len(item.event.raw_message_ids) == 2
        and item.event.id in chips
        and detail.recommendation is not None
        and detail.recommendation.type == "NO_ACTION"
        and one(pool, "select count(*) from public.recommendations where learner_id = %s", owner)[0]
        == 0,
        {
            "skills": len(ledger),
            "evidence_skill": detail.skill.canonical_name,
            "state": detail.mastery.state,
            "explanation": detail.mastery.explanation_code,
            "evidence": [
                (e.event.evidence_type, e.event.actor, e.event.outcome_signal)
                for e in detail.evidence
            ],
            "support": detail.mastery.support,
            "recommendation": detail.recommendation.type if detail.recommendation else None,
            "rows_written": 0,
        },
    )

    # The disposable learner (real Supabase Auth JWT), seeded on the existing course graph.
    supabase = auth(supabase_url, anon)
    email = f"skillmirror-p5-accept-{uuid.uuid4().hex[:12]}@mailinator.com"
    password = f"P5-{secrets.token_urlsafe(18)}"
    status, body = supabase.call("POST", "/auth/v1/signup", {"email": email, "password": password})
    assert status == 200 and body.get("access_token"), f"signup failed ({status})"
    learner, token = body["user"]["id"], body["access_token"]
    state |= {"learner_id": learner, "email": email}
    save(state)  # from here on, cleanup can always find the learner
    seed = seed_p5_learner_on_course(
        pool, uuid.UUID(learner), uuid.UUID(COURSE), {k: uuid.UUID(v) for k, v in roles.items()}
    )
    api = Http(args.api, token)
    report.check(
        "S",
        "disposable learner seeded on existing canonical skills, no registry row created",
        registry(pool) == state["registry"],
        {"learner": learner, "registry(nodes, edges, aliases, course_skills)": registry(pool)},
    )

    # 1. Dashboard state counts.
    entries = api.get(f"/v1/ledger?course_id={COURSE}")["skills"]
    counts: dict[str, int] = {}
    for e in entries:
        counts[e["mastery_state"]] = counts.get(e["mastery_state"], 0) + 1
    report.check(
        "1",
        "dashboard state counts",
        counts == {"DEMONSTRATED": 1, "DEVELOPING": 1, "EMERGING": 1, "UNKNOWN": 21},
        counts,
    )

    # 2. UNKNOWN remains neutral: no mean, no ledger row needed, no debt unless eligible.
    by_id = {e["skill_id"]: e for e in entries}
    unknown = [e for e in entries if e["mastery_state"] == "UNKNOWN"]
    report.check(
        "2",
        "UNKNOWN remains neutral",
        all(e["mastery_mean"] is None for e in unknown)
        and by_id[roles["while_loops"]]["ledger_version"] is None
        and {e["debt_band"] for e in unknown if e["skill_id"] != roles["comprehensions"]}
        == {"NONE"},
        {
            "unknown": len(unknown),
            "with_ledger_row": sum(e["ledger_version"] is not None for e in unknown),
        },
    )

    # 3. Skill map overlays the ledger on the existing course graph.
    graph = api.get(f"/v1/courses/{COURSE}/skills")
    assessable = {
        s["skill"]["id"]
        for s in graph["skills"]
        if s["skill"]["node_kind"] in ("SKILL", "SUBSKILL")
    }
    overlay = {k: by_id[v]["mastery_state"] for k, v in roles.items()}
    report.check(
        "3",
        "skill map overlays ledger state",
        assessable == set(by_id)
        and overlay
        == {
            "for_loops": "DEMONSTRATED",
            "while_loops": "UNKNOWN",
            "comprehensions": "UNKNOWN",
            "variables": "EMERGING",
            "indexing": "DEVELOPING",
        },
        overlay,
    )

    # 4. Skill detail + "Why?" provenance.
    details = {k: api.get(f"/v1/skills/{v}") for k, v in roles.items()}
    for_loops = details["for_loops"]
    traced = 0
    items = [i for d in details.values() for i in d["evidence"]]
    for i in items:
        ids = i["source"]["raw_message_ids"]
        activity_rows = api.get("/v1/activity?" + "&".join(f"raw_message_id={r}" for r in ids))[
            "items"
        ]
        chip_ids = [
            m["evidence_id"] for r in activity_rows for s in r["segments"] for m in s["mappings"]
        ]
        traced += int({r["id"] for r in activity_rows} == set(ids) and i["event"]["id"] in chip_ids)
    leak = any(w in json.dumps(d) for d in details.values() for w in ("policy_snapshot", "prompt"))
    codes = {
        k: (d["mastery"]["state"], d["mastery"]["explanation_code"], d["recommendation"]["type"])
        for k, d in details.items()
    }
    report.check(
        "4",
        "skill detail + Why? provenance",
        all(g["met"] for g in for_loops["mastery"]["gates"])
        and len(for_loops["evidence"]) == 5
        and sum(i["event"]["excluded"] for i in for_loops["evidence"]) == 1
        and all(
            i["event"]["mapping_confidence"] == 0.9 and i["event"]["attribution_confidence"] == 0.95
            for i in items
        )
        and traced == len(items) == 13
        and not leak,
        {"states": codes, "traced": f"{traced}/{len(items)}"},
    )

    # 5. Debt explanation: a reliance signal with factors; the others show none.
    debt = details["comprehensions"]["debt"]
    text = json.dumps([d["debt"] for d in details.values()]).lower()
    report.check(
        "5",
        "debt explanation",
        debt["eligible"]
        and debt["band"] in ("MODERATE", "HIGH")
        and len(debt["factors"]) == 5
        and debt["eligibility_code"] == "ELIGIBLE"
        and {k: d["debt"]["band"] for k, d in details.items() if k != "comprehensions"}
        == dict.fromkeys(("for_loops", "while_loops", "variables", "indexing"), "NONE")
        and not any(w in text for w in FORBIDDEN_WORDS),
        {
            "band": debt["band"],
            "factors": {f["code"]: f["level"] for f in debt["factors"]},
            "verification": debt["verification"],
        },
    )

    # 6. Activity enrichment.
    feed = api.get("/v1/activity?limit=200")["items"]
    anchors = [r for r in feed if r["segments"]]
    chips = [m for r in anchors for s in r["segments"] for m in s["mappings"]]
    report.check(
        "6",
        "activity enrichment",
        len(feed) == 26
        and len(anchors) == 13
        and {(s["route"], s["mapping_outcome"]) for r in anchors for s in r["segments"]}
        == {("MAP", "MAPPED")}
        and all(c["status"] == "ACCEPTED" and c["actor"] and c["evidence_type"] for c in chips)
        and sum(c["excluded"] for c in chips) == 1
        and all(r["analyzed_in"] for r in feed),
        {
            "rows": len(feed),
            "anchors": len(anchors),
            "actors": sorted({c["actor"] for c in chips}),
            "evidence_types": sorted({c["evidence_type"] for c in chips}),
            "excluded": sum(c["excluded"] for c in chips),
        },
    )

    state |= {
        "provenance": provenance(pool, learner),
        "ledger_versions": {k: by_id[v]["ledger_version"] for k, v in roles.items()},
        "feedback_count": one(
            pool, "select count(*) from public.feedback where user_id = %s", learner
        )[0],
        "seed_excluded": str(seed.excluded_evidence_id),
        "comprehension_evidence": [str(e) for e in seed.evidence("comprehensions")],
        "prepare": report.results,
    }
    write_ui_files(OUT / "ui-learner.env", email, password, ui_expectations(api, COURSE, roles))
    (OUT / "ui-learner.password").write_text(password, encoding="utf-8")
    save(state)
    return report


def verify(args, pool, supabase_url: str, anon: str) -> Report:
    report = Report()
    state = json.loads(STATE.read_text(encoding="utf-8"))
    learner, roles = state["learner_id"], state["roles"]
    token = sign_in(
        auth(supabase_url, anon),
        state["email"],
        (OUT / "ui-learner.password").read_text(encoding="utf-8"),
    )
    api = Http(args.api, token)
    feedback = rows(
        pool,
        "select id, action::text, target_type::text, target_id, skill_id, excluded_evidence_ids, "
        "recomputed_skill_ids, client_request_id from public.feedback where user_id = %s order by created_at",
        learner,
    )
    ui = [f for f in feedback if not f[7].startswith("p5-seed-")]

    # 7. DONT_COUNT (applied in the browser) -> evidence excluded -> ledger recomputed.
    dont = next((f for f in ui if f[1] == "DONT_COUNT"), None)
    excluded = (
        rows(
            pool,
            "select excluded, exclusion_reason from public.evidence_events where id = any(%s)",
            dont[5],
        )
        if dont
        else []
    )
    ledger = {e["skill_id"]: e for e in api.get(f"/v1/ledger?course_id={COURSE}")["skills"]}
    for_loops = ledger[roles["for_loops"]]
    report.check(
        "7",
        "DONT_COUNT -> evidence excluded -> ledger recomputed",
        dont is not None
        and dont[2] == "EVIDENCE_EVENT"
        and len(dont[5]) == 1
        and excluded == [(True, "LEARNER_DONT_COUNT")]
        and for_loops["mastery_state"] == "DEVELOPING"
        and for_loops["ledger_version"] > state["ledger_versions"]["for_loops"],
        {
            "feedback": str(dont[0]) if dont else None,
            "state": f"DEMONSTRATED -> {for_loops['mastery_state']}",
            "ledger_version": f"{state['ledger_versions']['for_loops']} -> {for_loops['ledger_version']}",
        },
    )

    # 8. WRONG_SKILL (applied in the browser) -> stored -> evidence excluded -> provenance kept.
    wrong = next((f for f in ui if f[1] == "WRONG_SKILL"), None)
    wrong_excluded = (
        rows(
            pool,
            "select excluded, exclusion_reason from public.evidence_events where id = any(%s)",
            wrong[5],
        )
        if wrong
        else []
    )
    mapping = (
        rows(
            pool, "select status::text, skill_id from public.skill_mappings where id = %s", wrong[3]
        )
        if wrong
        else []
    )
    report.check(
        "8",
        "WRONG_SKILL -> feedback stored -> evidence excluded -> provenance retained",
        wrong is not None
        and wrong[2] == "SKILL_MAPPING"
        and str(wrong[4]) == roles["comprehensions"]
        and wrong_excluded == [(True, "LEARNER_WRONG_SKILL")]
        and mapping == [("ACCEPTED", uuid.UUID(roles["comprehensions"]))]
        and provenance(pool, learner) == state["provenance"],
        {
            "feedback": str(wrong[0]) if wrong else None,
            "provenance": provenance(pool, learner),
            "mapping": "unchanged, ACCEPTED",
        },
    )

    # 9. Recommendations refresh deterministically.
    first = [
        (r["skill_id"], r["type"], r["priority"])
        for r in api.get("/v1/recommendations")["recommendations"]
    ]
    second = [
        (r["skill_id"], r["type"], r["priority"])
        for r in api.get("/v1/recommendations")["recommendations"]
    ]
    remaining = [
        e
        for e in state["comprehension_evidence"]
        if not one(pool, "select excluded from public.evidence_events where id = %s", e)[0]
    ]
    body = {"action": "WRONG_SKILL", "target_type": "EVIDENCE_EVENT", "target_id": remaining[0]}
    key = f"p5-hosted-{uuid.uuid4().hex[:10]}"
    status, created = api.call("POST", "/v1/feedback", body, {"Idempotency-Key": key})
    after = {
        r["skill_id"]: r
        for r in api.get("/v1/recommendations?include_no_action=true")["recommendations"]
    }
    names = {r["skill_id"]: r["canonical_name"] for r in after.values()}
    report.check(
        "9",
        "recommendations refresh deterministically",
        first == second
        and dict((s, t) for s, t, _ in first).get(roles["comprehensions"]) == "VERIFY"
        and dict((s, t) for s, t, _ in first).get(roles["for_loops"]) == "PRACTICE"
        and status == 201
        and after[roles["comprehensions"]]["type"] == "NO_ACTION"
        and after[roles["comprehensions"]]["debt_band"] == "NONE",
        {
            "queue": [(names.get(s, s), t, p) for s, t, p in first],
            "after_third_wrong_skill": after[roles["comprehensions"]]["type"],
        },
    )

    # 10. Retrying feedback is idempotent.
    before = one(pool, "select count(*) from public.feedback where user_id = %s", learner)[0]
    replay_status, replay = api.call("POST", "/v1/feedback", body, {"Idempotency-Key": key})
    repeat_status, repeat = api.call(
        "POST", "/v1/feedback", body, {"Idempotency-Key": f"{key}-new"}
    )
    conflict_status, _ = api.call(
        "POST",
        "/v1/feedback",
        {
            "action": "EVALUATION",
            "target_type": "SKILL",
            "target_id": roles["comprehensions"],
            "verdict": "AGREE",
        },
        {"Idempotency-Key": key},
    )
    report.check(
        "10",
        "retrying feedback is idempotent",
        replay_status == 200
        and replay["created"] is False
        and replay["feedback"]["id"] == created["feedback"]["id"]
        and repeat_status == 200
        and repeat["feedback"]["id"] == created["feedback"]["id"]
        and conflict_status == 409
        and one(pool, "select count(*) from public.feedback where user_id = %s", learner)[0]
        == before,
        {
            "replay": replay_status,
            "same_target_new_key": repeat_status,
            "key_reuse_other_body": conflict_status,
            "feedback_rows": before,
        },
    )

    # 11 / 12. No model request of either kind.
    runs = model_runs(pool)
    report.check(
        "11",
        "zero Gemini generation calls",
        runs["generation"] == state["model_runs"]["generation"],
        f"generation {state['model_runs']['generation']} -> {runs['generation']}",
    )
    report.check(
        "12",
        "zero embedding calls",
        runs["embedding"] == state["model_runs"]["embedding"],
        f"embedding {state['model_runs']['embedding']} -> {runs['embedding']}",
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


def cleanup(pool, supabase_url: str) -> Report:
    report = Report()
    state = json.loads(STATE.read_text(encoding="utf-8"))
    learner = state["learner_id"]
    service_key = env_value(REPO / "apps" / "web" / ".env.e2e.local", "SUPABASE_SERVICE_ROLE_KEY")
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
    runs = model_runs(pool)
    report.check(
        "C", "model_runs unchanged (0 generation, 0 embedding)", runs == state["model_runs"], runs
    )
    (OUT / "ui-learner.password").unlink(missing_ok=True)
    (OUT / "ui-learner.env").unlink(missing_ok=True)
    state["cleanup"] = report.results
    save(state)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="SkillMirror P5 hosted acceptance (no model call)")
    parser.add_argument("phase", choices=("prepare", "verify", "cleanup"))
    parser.add_argument("--api", default="http://127.0.0.1:8001")
    args = parser.parse_args()
    settings = get_settings()
    supabase_url = str(settings.supabase_url).rstrip("/")
    anon = env_value(REPO / "apps" / "web" / ".env.local", "NEXT_PUBLIC_SUPABASE_ANON_KEY")
    pool = create_pool(settings.database_url.get_secret_value(), max_size=4)
    try:
        if args.phase == "prepare":
            report = prepare(args, pool, supabase_url, anon)
        elif args.phase == "verify":
            report = verify(args, pool, supabase_url, anon)
        else:
            report = cleanup(pool, supabase_url)
    finally:
        pool.close()
    evidence = OUT / f"evidence-{args.phase}.json"
    evidence.write_text(
        json.dumps({"passed": report.passed, "results": report.results}, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"P5 HOSTED {args.phase.upper()}:", "PASS" if report.passed else "FAIL")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
