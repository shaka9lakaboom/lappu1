"""End-to-end run on the replay provider (P9, H13; CI job "E2E replay"). LOCAL stack only.

Real FastAPI routes, the real durable worker and the real database - only the model is replaced:
turn analysis, attribution and embeddings are REPLAYED from the committed Flash-Lite recording
(benchmark/recordings/gemini-3.5-flash-lite.jsonl, real model output), and the one task whose
prompt carries a fresh id (VERIFICATION_GENERATION: the new session id) is answered by a
deterministic challenge. No Gemini key exists in this process; a replayed request that is not in
the recording fails as STALE_RECORDING (never answered with something else).

    prepare   seed the critical-gate fixture graphs (their embeddings replayed), create the E2E
              learner through the local Supabase Auth admin API (password sign-in) and enrol it
              as a STUDENT of the fixture SQL course; write the state file
    serve     create_app (every /v1 route) on :8000 + build_worker (turn pipeline, evidence,
              ledger, recommendations, verification jobs, daily sweep) on the replay gateway
    verify    database checks after the browser flow (apps/web/e2e/p9-replay-e2e.spec.ts):
              0 real provider requests, 0 stale-recording misses, the evidence chain, the
              VERIFICATION evidence and the ledger / recommendation update
    cleanup   delete the learner (Auth admin: the normal cascade) and the fixture graphs

    cd services/backend
    # E2E_DATABASE_URL, E2E_SUPABASE_URL, E2E_SERVICE_ROLE_KEY, E2E_JWT_SECRET: the local stack
    # (`npx supabase status -o env`); refused for any non-local database
    python scripts/e2e_replay.py prepare
    python scripts/e2e_replay.py serve        # background; then the Playwright spec
    python scripts/e2e_replay.py verify
    python scripts/e2e_replay.py cleanup

The fixture flow is the critical gate's DEBT-18 (three "write the query for me" GROUP BY turns:
repeated delegation -> actionable debt -> VERIFY), sent as the extension sends it.
"""

import json
import os
import re
import secrets
import sys
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parents[1]
RUNNERS = REPO / "benchmark" / "runners"
for path in (BACKEND, RUNNERS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import gate_db  # noqa: E402

from app.ingestion.fingerprint import content_hash  # noqa: E402
from app.intelligence.verification.generator import (  # noqa: E402
    SYSTEM_PROMPT as VERIFICATION_SYSTEM,
)
from app.model_gateway import InMemoryResultCache, ModelGateway  # noqa: E402
from app.model_gateway.recorder import DbModelRunRecorder  # noqa: E402
from app.model_gateway.replay import ReplayProvider, assert_local_database  # noqa: E402
from app.model_gateway.types import ProviderTextResponse  # noqa: E402

OUT = Path(os.environ.get("E2E_OUT") or REPO / "test-results" / "e2e-replay")
STATE = OUT / "state.json"
RECORDING = REPO / "benchmark" / "recordings" / "gemini-3.5-flash-lite.jsonl"
FIXTURES = REPO / "benchmark" / "fixtures" / "critical-gate-courses.json"
CASES = REPO / "benchmark" / "cases" / "critical-gate" / "DEBT.jsonl"
LIVE_MODEL = "gemini-3.5-flash-lite"
EMBEDDING_MODEL = "gemini-embedding-2"
PROVIDER_NAME = "replay"  # model_runs: a replayed run is never a real request of the model
CASE = "DEBT-18"
COURSE_KEY = "sql"
SKILL_KEY = "sql-group-by"
PORT = int(os.environ.get("E2E_API_PORT", "8000"))

# The deterministic challenge (the spec answers it; the key stays server-side as always).
CHALLENGE_PROMPT = (
    "A bakery chain stores every sale as one row with the shop name and the amount. The owner "
    "wants one line per shop showing that shop's total takings. Which clause must the query add?"
)
CHALLENGE_CHOICES = [
    {"key": "A", "text": "ORDER BY shop"},
    {"key": "B", "text": "GROUP BY shop, with SUM(amount) in the select list"},
    {"key": "C", "text": "WHERE shop IS NOT NULL"},
]
CORRECT_TEXT = "GROUP BY shop"


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(
            f"{name} is not set (the local Supabase stack: npx supabase status -o env)"
        )
    return value


def save(state: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def load() -> dict:
    return json.loads(STATE.read_text(encoding="utf-8"))


def database_url() -> str:
    url = env("E2E_DATABASE_URL")
    assert_local_database(url)
    return url


def fixtures():
    spec = gate_db.load_fixture_spec(FIXTURES)
    return spec, gate_db.fixtures_from_spec(spec)


def case_turns() -> list[dict[str, Any]]:
    for line in CASES.read_text(encoding="utf-8").splitlines():
        case = json.loads(line)
        if case["id"] == CASE:
            # The hashes the ingestion API checks (app.ingestion.fingerprint, as the extension).
            return [
                {
                    "user": t["user"],
                    "assistant": t["assistant"],
                    "user_hash": content_hash(t["user"]),
                    "assistant_hash": content_hash(t["assistant"]),
                }
                for t in case["turns"]
            ]
    raise SystemExit(f"{CASE} not found in {CASES}")


class E2EProvider:
    """The recording for every replayable request; a deterministic challenge for verification
    generation (its prompt names the new session). Anything else missing is STALE_RECORDING."""

    def __init__(self, replay: ReplayProvider) -> None:
        self._replay = replay
        self.name = PROVIDER_NAME
        self.challenges = 0

    @property
    def misses(self) -> list[str]:
        return self._replay.misses

    def generate_json(self, *, model, system, messages, json_schema, timeout):
        if system == VERIFICATION_SYSTEM:
            self.challenges += 1
            return ProviderTextResponse(text=json.dumps(challenge_for(messages[-1].content)))
        return self._replay.generate_json(
            model=model, system=system, messages=messages, json_schema=json_schema, timeout=timeout
        )

    def embed(self, **kwargs):
        return self._replay.embed(**kwargs)


def challenge_for(text: str) -> dict[str, Any]:
    """A valid VERIFICATION_GENERATION answer for the planned skill and difficulty."""
    skill = re.search(r"- skill_id=([0-9a-f-]{36})", text).group(1)
    planned = float(re.search(r"Planned difficulty: ([0-9.]+)", text).group(1))
    return {
        "skill_id": skill,
        "difficulty": planned,
        "assessment_type": "mcq",
        "prompt": CHALLENGE_PROMPT,
        "choices": CHALLENGE_CHOICES,
        "expected_answer": "B",
        "rubric": [],
        "prerequisites_used": [],
        "transfer_distance": "medium",
        "estimated_minutes": 2,
    }


def replay_gateway(pool) -> tuple[ModelGateway, E2EProvider]:
    provider = E2EProvider(ReplayProvider.from_file(RECORDING, name=PROVIDER_NAME))
    gateway = ModelGateway(
        provider,
        DbModelRunRecorder(pool),
        generation_model=LIVE_MODEL,
        embedding_model=EMBEDDING_MODEL,
        default_timeout=90,
        result_cache=InMemoryResultCache(4096),
        embedding_cache=InMemoryResultCache(4096),
    )
    return gateway, provider


def auth_admin(method: str, path: str, body: dict | None = None) -> dict:
    base = env("E2E_SUPABASE_URL").rstrip("/")
    if not base.startswith(("http://127.0.0.1", "http://localhost")):
        raise SystemExit("E2E_SUPABASE_URL must be the local stack")
    request = Request(  # noqa: S310 - http(s) to the local stack only (checked above)
        base + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "apikey": env("E2E_SERVICE_ROLE_KEY"),
            "Authorization": f"Bearer {env('E2E_SERVICE_ROLE_KEY')}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - the local stack
            raw = response.read()
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        raise SystemExit(f"Supabase Auth admin {method} {path}: HTTP {exc.code}") from exc


# --- phases ---------------------------------------------------------------------------------------


def prepare() -> None:
    from app.db.pool import create_pool

    pool = create_pool(database_url(), max_size=3)
    spec, fx = fixtures()
    try:
        with pool.connection() as conn:
            foreign = gate_db.foreign_registry_rows(conn, fx)
        if foreign:
            raise SystemExit(
                f"{foreign} ACTIVE registry node(s) besides the fixtures would change the "
                "replayed retrieval pools: use a fresh local database (npx supabase db reset)"
            )
        gateway, provider = replay_gateway(pool)
        embedded = gate_db.seed_fixtures(pool, gateway, spec, fx)
        if provider.misses:
            raise SystemExit(f"stale recording: {len(provider.misses)} fixture embedding miss(es)")
        email = f"e2e-replay-{uuid.uuid4().hex[:10]}@example.test"
        password = f"E2e-{secrets.token_urlsafe(18)}"
        user = auth_admin(
            "POST",
            "/auth/v1/admin/users",
            {"email": email, "password": password, "email_confirm": True},
        )
        learner = user["id"]
        course = fx.courses[COURSE_KEY]
        with pool.connection() as conn, conn.transaction():
            conn.execute(
                "insert into public.course_memberships (course_id, user_id, role) "
                "values (%s, %s, 'STUDENT')",
                (course, learner),
            )
        save(
            {
                "started_at": datetime.now(UTC).isoformat(),
                "email": email,
                "password": password,
                "learner_id": learner,
                "course_id": str(course),
                "course_name": next(c["name"] for c in spec["courses"] if c["key"] == COURSE_KEY),
                "skill_id": str(fx.skills[SKILL_KEY]),
                "turns": case_turns(),
                "correct_choice": CORRECT_TEXT,
                "api": f"http://localhost:{PORT}",
            }
        )
        print(f"prepare: fixtures seeded ({embedded} embedded), learner + membership created")
    finally:
        pool.close()


def serve() -> None:
    import uvicorn

    from app.core.config import Settings
    from app.db.pool import get_db_pool
    from app.jobs.worker import build_worker
    from app.main import create_app

    url = database_url()
    settings = Settings(
        _env_file=None,  # never services/backend/.env: no hosted value, no Gemini key
        app_env="development",
        database_url=url,
        supabase_url=env("E2E_SUPABASE_URL"),
        supabase_jwt_secret=env("E2E_JWT_SECRET"),
        cors_origins=os.environ.get("E2E_CORS_ORIGINS", "http://localhost:3000"),
        worker_enabled=False,  # the app's own Gemini worker never starts; ours runs below
    )
    if settings.gemini_api_key is not None:
        raise SystemExit("the E2E server must not have a Gemini key")
    app = create_app(settings)
    pool = get_db_pool(settings)
    gateway, provider = replay_gateway(pool)
    worker = build_worker(pool, gateway, turn_analysis_mode="combined", batch_size=4)
    stop = threading.Event()
    thread = threading.Thread(
        target=worker.run_forever, args=(stop, 0.5), name="e2e-worker", daemon=True
    )
    thread.start()
    print(f"serve: API on :{PORT}, worker on the replay provider ({RECORDING.name})", flush=True)
    try:
        uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")
    finally:
        stop.set()
        thread.join(timeout=10)
        print(
            f"serve: stopped; replay misses {len(provider.misses)}, "
            f"deterministic challenges {provider.challenges}",
            flush=True,
        )


def check(results: list, name: str, ok: bool, detail: object) -> None:
    results.append({"check": name, "passed": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def verify() -> int:
    import psycopg

    state = load()
    learner, skill = state["learner_id"], state["skill_id"]
    since = datetime.fromisoformat(state["started_at"]) - timedelta(seconds=5)
    results: list = []
    with psycopg.connect(database_url(), prepare_threshold=None) as conn:
        conn.execute("set transaction read only")
        runs = conn.execute(
            "select provider, status::text, coalesce(error_code, ''), task_type, "
            "cache_source_run_id is not null from public.model_runs where created_at >= %s",
            (since,),
        ).fetchall()
        check(
            results,
            "0 real provider requests (every model run is the replay provider)",
            runs and all(r[0] == PROVIDER_NAME for r in runs),
            {"runs": len(runs), "providers": sorted({r[0] for r in runs})},
        )
        check(
            results,
            "0 stale-recording misses, 0 failed model runs",
            all(r[1] == "SUCCEEDED" for r in runs),
            sorted({(r[3], r[1], r[2]) for r in runs if r[1] != "SUCCEEDED"}),
        )
        tasks = sorted({r[3] for r in runs})
        check(
            results,
            "turn analysis, attribution, embeddings and verification generation all ran",
            {"TURN_ANALYSIS", "SKILL_ATTRIBUTION", "VERIFICATION_GENERATION"} <= set(tasks),
            tasks,
        )
        (raw,) = conn.execute(
            "select count(*) from public.raw_messages where learner_id = %s", (learner,)
        ).fetchone()
        jobs = dict(
            conn.execute(
                "select outcome, count(*) from public.processing_jobs "
                "where learner_id = %s and job_type = 'PROCESS_RAW_MESSAGE' group by 1",
                (learner,),
            ).fetchall()
        )
        check(
            results,
            "6 captured messages processed (3 turns)",
            raw == 6 and jobs.get("EVIDENCE_RECORDED") == 3,
            {"raw_messages": raw, "outcomes": jobs},
        )
        delegations = conn.execute(
            "select count(*) from public.evidence_events where learner_id = %s and skill_id = %s "
            "and source_type = 'AI_ACTIVITY' and actor = 'AI' and evidence_type = 'OBSERVATION'",
            (learner, skill),
        ).fetchone()[0]
        check(
            results,
            "AI delegation evidence on GROUP BY (no mastery credit)",
            delegations >= 2,
            delegations,
        )
        verification = conn.execute(
            "select s.state::text, r.pass, e.id is not null from public.verification_sessions s "
            "left join public.verification_results r on r.session_id = s.id "
            "left join public.evidence_events e on e.source_type = 'VERIFICATION' and e.source_id = r.id "
            "where s.learner_id = %s and s.skill_id = %s",
            (learner, skill),
        ).fetchall()
        check(
            results,
            "one verification: EVALUATED, passed, with its VERIFICATION evidence",
            verification == [("EVALUATED", True, True)],
            verification,
        )
        ledger = conn.execute(
            "select mastery_state::text, debt_eligible, debt_score, evidence_count, "
            "debt_components ->> 'verification' from public.skill_ledger "
            "where learner_id = %s and skill_id = %s",
            (learner, skill),
        ).fetchone()
        check(
            results,
            "ledger re-derived from the VERIFICATION evidence (recently passed, debt below 15)",
            ledger is not None and ledger[4] == "RECENTLY_PASSED" and float(ledger[2]) < 15,
            ledger,
        )
        recs = conn.execute(
            "select type::text, state::text, reason_code from public.recommendations "
            "where learner_id = %s and skill_id = %s order by created_at",
            (learner, skill),
        ).fetchall()
        check(
            results,
            "the VERIFY recommendation is completed; the active action is no longer VERIFY",
            ("VERIFY", "COMPLETED") in {(r[0], r[1]) for r in recs}
            and all(r[0] != "VERIFY" for r in recs if r[1] == "ACTIVE"),
            recs,
        )
        conn.rollback()
    passed = all(r["passed"] for r in results)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "verify.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(
        f"verify: {'PASS' if passed else 'FAIL'} ({sum(r['passed'] for r in results)}/{len(results)})"
    )
    return 0 if passed else 1


def cleanup() -> None:
    from app.db.pool import create_pool

    pool = create_pool(database_url(), max_size=2)
    _, fx = fixtures()
    learners = []
    if STATE.exists():
        state = load()
        learners = [uuid.UUID(state["learner_id"])]
        auth_admin("DELETE", f"/auth/v1/admin/users/{state['learner_id']}")
    try:
        gate_db.remove_fixtures(pool, fx, learners)
    finally:
        pool.close()
    print("cleanup: learner deleted (Auth cascade), fixture graphs removed")


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "prepare":
        prepare()
    elif command == "serve":
        serve()
    elif command == "verify":
        return verify()
    elif command == "cleanup":
        cleanup()
    else:
        print("usage: e2e_replay.py prepare|serve|verify|cleanup")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
