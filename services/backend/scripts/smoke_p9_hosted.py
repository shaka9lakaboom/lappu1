"""P9 hosted smoke on the final code (manual gate; never in CI). No model call.

    readonly       READ ONLY / rolled back: migrations exactly 0001-0009; the three benchmark rows
                   as /admin/benchmark presents them (LIVE: hard gates PASS, 71 / 72, provider /
                   transport failure REL-06, stored verdict FAIL - the rows unchanged); the READY
                   check 3aa4481e is NOT_NEEDED for its learner through the real
                   list_verifications (run inside a transaction that is rolled back: it refreshes
                   and plans); the post-sweep ledger; the "what is python" unit's classification
    admin          a disposable ADMIN (Auth signup @mailinator.com + the operator's audited
                   grant_role) for the /admin/benchmark browser look (apps/web/e2e/p8-smoke.spec.ts,
                   its admin test - P9 assertions); walkthrough env to test-results/p9-hosted/
    admin-cleanup  delete that admin through the Auth admin API (the cascade); its ROLE_CHANGE audit
                   row stays (actor null), as in P8

    cd services/backend
    .venv/Scripts/python scripts/smoke_p9_hosted.py readonly
    .venv/Scripts/python scripts/smoke_p9_hosted.py admin
    (API :8001 with a blank GEMINI_API_KEY + WORKER_ENABLED=false, web :3001 -> hosted; the spec)
    .venv/Scripts/python scripts/smoke_p9_hosted.py admin-cleanup

Values come from services/backend/.env, apps/web/.env.local and apps/web/.env.e2e.local; none is
printed. Evidence: test-results/p9-hosted/ (git-ignored; ids and codes only).
"""

import argparse
import json
import secrets
import sys
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import psycopg  # noqa: E402
from acceptance_p5_hosted import env_value  # noqa: E402
from grant_role import change_role  # noqa: E402

from app.admin.monitoring import list_benchmark_runs  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.experience.verifications import list_verifications  # noqa: E402
from app.intelligence.mastery.engine import ALGORITHM_VERSION  # noqa: E402
from app.intelligence.policy import load_policy  # noqa: E402

OUT = REPO / "test-results" / "p9-hosted"
STATE = OUT / "smoke-state.json"
EXPECTED_MIGRATIONS = [f"{n:04d}" for n in range(1, 10)]
AFFECTED_LEARNER = "8afbd2c8"
READY_SESSION = "3aa4481e"
LIVE_RUN = "49359994-37d5-48b9-94b9-8b20b9fc4ece"


class Report:
    def __init__(self) -> None:
        self.results: list[dict] = []

    def check(self, number: str, name: str, ok: bool, detail: object) -> None:
        self.results.append({"check": number, "name": name, "passed": bool(ok), "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {number:>3} {name}: {detail}")

    @property
    def passed(self) -> bool:
        return all(r["passed"] for r in self.results)


def load() -> dict:
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def save(state: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def readonly(conn: psycopg.Connection, _args) -> Report:
    report = Report()
    with conn.transaction():
        conn.execute("set transaction read only")
        migrations = [
            r[0]
            for r in conn.execute(
                "select version from supabase_migrations.schema_migrations order by 1"
            ).fetchall()
        ]
        report.check(
            "R1",
            "hosted migrations exactly 0001-0009",
            migrations == EXPECTED_MIGRATIONS,
            migrations,
        )
        stored = conn.execute(
            "select mode::text, verdict::text, passed_count, case_count from public.benchmark_runs "
            "order by created_at"
        ).fetchall()
        runs = list_benchmark_runs(conn)
        live = next((r for r in runs.runs if str(r.id) == LIVE_RUN), None)
        report.check(
            "R2",
            "benchmark rows unchanged: DETERMINISTIC 120/120 PASS, REPLAY 72/72 PASS, LIVE 71/72 FAIL",
            stored
            == [
                ("DETERMINISTIC", "PASS", 120, 120),
                ("REPLAY", "PASS", 72, 72),
                ("LIVE", "FAIL", 71, 72),
            ],
            stored,
        )
        report.check(
            "R3",
            "LIVE presented as hard gates PASS 13/13, 1 provider / transport failure (REL-06), FAIL",
            live is not None
            and live.verdict == "FAIL"
            and live.hard_gates_failed == []
            and live.hard_gates_total == 13
            and live.failed_without_hard_gate == 1
            and live.provider_failure_cases == ["REL-06"],
            {
                "gates": f"{live.hard_gates_total - len(live.hard_gates_failed)}/{live.hard_gates_total}",
                "completion": f"{live.passed_count}/{live.case_count}",
                "provider": live.provider_failure_cases,
                "verdict": live.verdict,
                "source": (live.case_errors_source or "")[:60],
            }
            if live
            else None,
        )
        versions = [
            r[0]
            for r in conn.execute(
                "select distinct algorithm_version from public.skill_ledger order by 1"
            ).fetchall()
        ]
        report.check(
            "R4",
            f"every ledger row on {ALGORITHM_VERSION} (the P9 sweep)",
            versions == [ALGORITHM_VERSION],
            versions,
        )
        unit = conn.execute(
            "select s.route::text, s.route_reason, s.learning_relevance::text, s.intent::text "
            "from public.activity_segments s join public.raw_messages m on m.id = s.anchor_message_id "
            "where m.learner_id::text like %s and lower(m.content_text) like %s",
            (AFFECTED_LEARNER + "%", "%what is python%"),
        ).fetchall()
        report.check(
            "R5",
            "'what is python': recorded STOP with learning relevance low -> 'Low learning relevance - no skill evidence'",
            unit == [("STOP", "NON_LEARNING", "low", "learn")],
            unit,
        )

    # list_verifications refreshes recommendations and plans (writes): inside a rolled-back
    # transaction it shows exactly what the learner sees, and nothing persists.
    learner = conn.execute(
        "select id from public.profiles where id::text like %s", (AFFECTED_LEARNER + "%",)
    ).fetchone()[0]
    before = conn.execute(
        "select md5(coalesce(json_agg(t order by t::text)::text, '')) from public.verification_sessions t "
        "where t.learner_id = %s",
        (learner,),
    ).fetchone()[0]
    with conn.transaction() as tx:
        queue = list_verifications(conn, learner, policy=load_policy(conn))
        raise_rollback = psycopg.Rollback(tx)
        stale = [s for s in queue.not_needed if str(s.id).startswith(READY_SESSION)]
        detail = {
            "ready": [str(s.id)[:8] for s in queue.ready],
            "not_needed": [(str(s.id)[:8], s.status, s.state) for s in queue.not_needed],
            "budget": queue.budget.model_dump(),
            "planned": [str(s.id)[:8] for s in queue.preparing],
        }
        raise raise_rollback
    after = conn.execute(
        "select md5(coalesce(json_agg(t order by t::text)::text, '')) from public.verification_sessions t "
        "where t.learner_id = %s",
        (learner,),
    ).fetchone()[0]
    report.check(
        "R6",
        f"READY {READY_SESSION} is NOT_NEEDED: not a ready check, not in the budget; nothing persisted",
        len(stale) == 1
        and not queue.ready
        and not queue.preparing
        and stale[0].state == "READY"
        and before == after,
        detail,
    )
    conn.rollback()
    return report


def http(url: str, key: str, method: str = "GET", body: dict | None = None):
    request = Request(  # noqa: S310 - the project's own Supabase Auth endpoints
        url,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310
        raw = response.read()
        return response.status, (json.loads(raw) if raw else {})


def admin(conn: psycopg.Connection, args) -> Report:
    report = Report()
    state = load()
    if state.get("admin_id"):
        raise SystemExit("a disposable admin already exists: run admin-cleanup first")
    anon = env_value(REPO / "apps" / "web" / ".env.local", "NEXT_PUBLIC_SUPABASE_ANON_KEY")
    email = f"skillmirror-p9-admin-{uuid.uuid4().hex[:12]}@mailinator.com"
    password = f"P9-{secrets.token_urlsafe(18)}"
    status, body = http(
        f"{args.supabase_url}/auth/v1/signup", anon, "POST", {"email": email, "password": password}
    )
    admin_id = body.get("user", {}).get("id")
    save({**state, "admin_id": admin_id, "admin_email": email})
    old, new = change_role(conn, uuid.UUID(admin_id), "ADMIN")
    OUT.mkdir(parents=True, exist_ok=True)
    expect = OUT / "benchmark-expect.json"
    expect.write_text(json.dumps({"live_run": LIVE_RUN}), encoding="utf-8")
    (OUT / "ui-admin.env").write_text(
        f"P8_ADMIN_EMAIL={email}\nP8_ADMIN_PASSWORD={password}\nP8_BENCHMARK_EXPECT={expect.as_posix()}\n"
        f"P8_SMOKE_OUT={OUT.as_posix()}\n",
        encoding="utf-8",
    )
    report.check(
        "A1",
        "disposable admin signed up and promoted (operator, audited)",
        status == 200 and (old, new) == ("STUDENT", "ADMIN"),
        {"admin": admin_id[:8]},
    )
    return report


def admin_cleanup(conn: psycopg.Connection, args) -> Report:
    report = Report()
    state = load()
    admin_id = state["admin_id"]
    service = env_value(REPO / "apps" / "web" / ".env.e2e.local", "SUPABASE_SERVICE_ROLE_KEY")
    status, _ = http(f"{args.supabase_url}/auth/v1/admin/users/{admin_id}", service, "DELETE")
    left = conn.execute(
        "select (select count(*) from auth.users where id = %(a)s) + "
        "(select count(*) from public.profiles where id = %(a)s)",
        {"a": admin_id},
    ).fetchone()[0]
    audit = conn.execute(
        "select action::text, actor_id is null from public.audit_events where entity_id = %s",
        (admin_id,),
    ).fetchall()
    report.check(
        "D1",
        "disposable admin deleted (Auth cascade); its ROLE_CHANGE audit row stays",
        status == 200 and left == 0 and audit == [("ROLE_CHANGE", True)],
        {"delete": status, "left": left, "audit": audit},
    )
    (OUT / "ui-admin.env").unlink(missing_ok=True)
    state.pop("admin_id", None)
    save({**state, "admin_deleted": admin_id})
    return report


PHASES = {"readonly": readonly, "admin": admin, "admin-cleanup": admin_cleanup}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("phase", choices=PHASES)
    args = parser.parse_args()
    settings = get_settings()
    args.supabase_url = str(settings.supabase_url).rstrip("/")
    with psycopg.connect(settings.database_url.get_secret_value(), prepare_threshold=None) as conn:
        report = PHASES[args.phase](conn, args)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"smoke-{args.phase}.json").write_text(
        json.dumps({"passed": report.passed, "results": report.results}, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"{args.phase}: {'PASS' if report.passed else 'FAIL'}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
