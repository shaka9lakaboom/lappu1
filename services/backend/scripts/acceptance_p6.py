"""P6 local acceptance: the verification loop over deterministic fixtures (never run in CI).

No real model is called. The backend API runs WITHOUT GEMINI_API_KEY (so it has no ModelGateway
or worker); this script runs the worker itself, in-process, on a SCRIPTED FAKE provider (the same
production gateway, jobs, validator, graders and persistence), and checks that every model_runs
row created during the run is a fake-provider row.

    npx supabase@2.117.0 start -x realtime,storage-api,imgproxy,mailpit,postgres-meta,studio,edge-runtime,logflare,vector,supavisor
    cd services/backend
    # backend env: DATABASE_URL=<local>, SUPABASE_URL=http://127.0.0.1:54321, GEMINI_API_KEY empty,
    #              WORKER_ENABLED=false
    .venv/Scripts/python -m uvicorn app.main:app --port 8001
    .venv/Scripts/python scripts/acceptance_p6.py run --api http://127.0.0.1:8001 \
        --supabase-url http://127.0.0.1:54321 --anon-key <local anon key> \
        --database-url postgresql://postgres:postgres@127.0.0.1:54322/postgres \
        --out ../../test-results/p6-acceptance/evidence.json \
        --ui-credentials ../../test-results/p6-acceptance/ui-learner.env
    # browser walkthrough: keep the fake worker running while apps/web/e2e/p6-acceptance.spec.ts runs
    .venv/Scripts/python scripts/acceptance_p6.py worker --database-url <local> --seconds 600

Learners (signed up through Supabase Auth, real JWTs), each seeded by tests/p6_fixtures.py:

    A  the passing loop            VERIFY -> plan -> challenge -> start/resume -> idempotent submit
                                   -> deterministic grade -> evidence -> VERIFIED -> debt 0.2
                                   -> recommendation refresh -> replay creates nothing
    B  abandonment                 started, then stopped: no result, no evidence, no penalty
    C  failure                     a wrong answer: negative evidence, factor 1.0, no fabricated debt
    UI the browser walkthrough     credentials in a git-ignored file, never printed

Run it on a disposable LOCAL database (its fixture creates prefixed registry rows): `supabase db
reset` afterwards. The hosted run is acceptance_p6_hosted.py (existing graph, no registry rows).
"""

import argparse
import json
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.db.pool import create_pool  # noqa: E402
from app.intelligence.policy import load_policy  # noqa: E402
from app.intelligence.verification import persist  # noqa: E402
from app.jobs.worker import build_worker  # noqa: E402
from app.model_gateway import (  # noqa: E402
    DbResultCache,
    InMemoryResultCache,
    ModelGateway,
    TieredResultCache,
)
from app.model_gateway.recorder import DbModelRunRecorder  # noqa: E402
from tests.fakes import FakeProvider, challenge, route_responder  # noqa: E402
from tests.p6_fixtures import graded_verification, seed_p6_learner  # noqa: E402

FRESH_PROMPTS = (
    "A school keeps pupils and their club sign-ups in two tables. The head teacher wants a list of "
    "every pupil, also those who joined no club. Which join produces that list?",
    "A hospital report lists every ward with the patients currently admitted, and empty wards must "
    "appear too. Which join should the report use?",
    "An airline lists every aircraft with its scheduled flights; aircraft in maintenance have no "
    "flights but must still appear. Which join gives this result?",
    "A museum catalogue shows every artist with the paintings on display, including artists with "
    "nothing on show at the moment. Which join is needed?",
)
CHOICES = [
    {"key": "A", "text": "An INNER JOIN on the shared key"},
    {"key": "B", "text": "A LEFT JOIN from the table that must be listed in full"},
    {"key": "C", "text": "A CROSS JOIN of the two tables"},
]
CORRECT, WRONG = ["B"], ["A"]


class Http:
    def __init__(self, base: str, token: str | None = None, apikey: str | None = None) -> None:
        self.base, self.token, self.apikey = base.rstrip("/"), token, apikey

    def call(self, method: str, path: str, body: dict | None = None, headers: dict | None = None):
        request = urllib.request.Request(  # noqa: S310 - fixed local base URL
            f"{self.base}{path}",
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {self.token}"} if self.token else {}),
                **({"apikey": self.apikey} if self.apikey else {}),
                **(headers or {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"null")

    def get(self, path: str):
        status, body = self.call("GET", path)
        assert status == 200, f"GET {path} -> {status} {body}"
        return body


def sign_up(supabase: Http, label: str) -> tuple[str, str, str, str]:
    email = f"skillmirror-p6-{label}-{uuid.uuid4().hex[:10]}@example.test"
    password = f"P6-{secrets.token_urlsafe(18)}"
    status, body = supabase.call("POST", "/auth/v1/signup", {"email": email, "password": password})
    assert status == 200 and body.get("access_token"), f"signup failed ({status})"
    return body["user"]["id"], body["access_token"], email, password


def scripted_provider() -> FakeProvider:
    """VERIFICATION_GENERATION: a valid MCQ for the planned skill and difficulty (correct: B)."""
    counter = iter(range(10**6))

    def respond(messages):
        import re

        text = messages[-1].content
        skill = re.search(r"- skill_id=([0-9a-f-]{36})", text).group(1)
        planned = float(re.search(r"Planned difficulty: ([0-9.]+)", text).group(1))
        prompt = FRESH_PROMPTS[next(counter) % len(FRESH_PROMPTS)]
        return challenge(skill, difficulty=planned, prompt=prompt, choices=CHOICES)

    return FakeProvider(route_responder(verification=respond))


def fake_gateway(pool, provider: FakeProvider) -> ModelGateway:
    """The production gateway (durable cache, model_runs recorder) on the scripted fake provider."""
    return ModelGateway(
        provider,
        DbModelRunRecorder(pool),
        generation_model="fake-generation-model",
        embedding_model="fake-embedding-model",
        default_timeout=5,
        result_cache=TieredResultCache(InMemoryResultCache(), DbResultCache(pool)),
    )


def fake_worker(pool, provider: FakeProvider):
    return build_worker(pool, fake_gateway(pool, provider), batch_size=10)


def one(pool, sql: str, *params):
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchone()


def check(results: list, number: int, name: str, ok: bool, detail: object) -> None:
    results.append({"check": number, "name": name, "passed": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {number:>2} {name}: {detail}")


def ledger_row(api: Http, skill: str) -> dict:
    return next(s for s in api.get("/v1/ledger")["skills"] if s["skill_id"] == skill)


def session_counts(pool, learner: str) -> dict[str, int]:
    row = one(
        pool,
        """
        select (select count(*) from public.verification_sessions where learner_id = %s),
               (select count(*) from public.verification_items where learner_id = %s),
               (select count(*) from public.verification_results where learner_id = %s),
               (select count(*) from public.evidence_events
                 where learner_id = %s and source_type = 'VERIFICATION')
        """,
        learner,
        learner,
        learner,
        learner,
    )
    return dict(zip(("sessions", "items", "results", "evidence"), row, strict=True))


def open_check(api: Http, pool, worker, learner: str) -> str:
    """GET plans the check (no model call); the fake worker issues the challenge."""
    queue = api.get("/v1/verifications")
    assert len(queue["preparing"]) == 1, queue
    session = queue["preparing"][0]["id"]
    worker.drain()
    assert api.get(f"/v1/verifications/{session}")["session"]["state"] == "READY"
    return session


def run(args) -> int:
    pool = create_pool(args.database_url, max_size=6)
    supabase = Http(args.supabase_url, apikey=args.anon_key)
    started = datetime.now(UTC)
    health = Http(args.api).get("/health")
    provider = scripted_provider()
    worker = fake_worker(pool, provider)
    results: list[dict] = []
    tag = uuid.uuid4().hex[:6]
    learners = {label: sign_up(supabase, label) for label in ("a", "b", "c", "ui")}
    seeds = {
        label: seed_p6_learner(pool, UUID(learners[label][0]), f"P6{label}{tag} ")
        for label in learners
    }
    a_id, a_token, *_ = learners["a"]
    api = Http(args.api, a_token)
    skill = str(seeds["a"].skills["left_join"])

    # 1. A VERIFY recommendation exists for repeated, unverified delegation.
    rec = next(
        r for r in api.get("/v1/recommendations")["recommendations"] if r["skill_id"] == skill
    )
    before = ledger_row(api, skill)
    check(
        results,
        1,
        "VERIFY recommendation from actionable debt",
        rec["type"] == "VERIFY"
        and before["debt_actionable"]
        and before["mastery_state"] == "DEVELOPING",
        {"type": rec["type"], "debt": before["debt_score"], "state": before["mastery_state"]},
    )

    # 2. The planner creates one PLANNED session - with no model call in the request.
    requests_before = one(pool, "select count(*) from public.model_runs")[0]
    queue = api.get("/v1/verifications")
    again = api.get("/v1/verifications")
    requests_after_get = one(pool, "select count(*) from public.model_runs")[0]
    session = queue["preparing"][0]["id"] if queue["preparing"] else None
    check(
        results,
        2,
        "planner creates exactly one PLANNED session (no model call)",
        len(queue["preparing"]) == 1
        and len(again["preparing"]) == 1
        and queue["preparing"][0]["recommendation_id"] == rec["id"]
        and requests_after_get == requests_before,
        {"session": session, "model_runs": f"{requests_before} -> {requests_after_get}"},
    )

    # 3. Generated + validated challenge -> READY (one scripted generation request).
    stats = worker.drain()
    detail = api.get(f"/v1/verifications/{session}")
    item = one(
        pool,
        "select generation_attempt, validation, generation_model_run_id, "
        "prompt_fingerprint from public.verification_items where session_id = %s",
        session,
    )
    check(
        results,
        3,
        "challenge generated, validated -> READY",
        detail["session"]["state"] == "READY"
        and item[0] == 1
        and all(item[1]["checks"].values())
        and len(provider.calls) == 1,
        {"worker": str(stats), "attempt": item[0], "checks": item[1]["checks"]},
    )

    # 4. Start / resume; the answer key never reaches the learner.
    start = api.call("POST", f"/v1/verifications/{session}/start")
    resume = api.call("POST", f"/v1/verifications/{session}/start")
    leaked = any(k in json.dumps(start[1]) for k in ("expected_answer", "rubric"))
    check(
        results,
        4,
        "start -> IN_PROGRESS, start again resumes, no answer key exposed",
        start[0] == 200
        and resume[0] == 200
        and not leaked
        and start[1]["session"]["state"] == "IN_PROGRESS"
        and resume[1]["challenge"]["item_id"] == start[1]["challenge"]["item_id"],
        {"state": resume[1]["session"]["state"], "item": resume[1]["challenge"]["item_id"]},
    )

    # 5. Idempotent submission, stored before grading.
    key = f"p6acc-{tag}-a"
    first = api.call(
        "POST",
        f"/v1/verifications/{session}/submit",
        {"selected": CORRECT},
        {"Idempotency-Key": key},
    )
    replay = api.call(
        "POST",
        f"/v1/verifications/{session}/submit",
        {"selected": CORRECT},
        {"Idempotency-Key": key},
    )
    conflict = api.call(
        "POST", f"/v1/verifications/{session}/submit", {"selected": WRONG}, {"Idempotency-Key": key}
    )
    stored = one(
        pool,
        "select state::text, submitted_response from public.verification_sessions where id = %s",
        session,
    )
    check(
        results,
        5,
        "submission persisted idempotently before grading",
        (first[0], replay[0], conflict[0]) == (202, 200, 409)
        and stored == ("SUBMITTED", {"selected": ["B"]}),
        {"statuses": [first[0], replay[0], conflict[0]], "stored": stored},
    )

    # 6. Deterministic grading: no evaluation request.
    calls_before = len(provider.calls)
    worker.drain()
    graded = api.get(f"/v1/verifications/{session}")
    result = graded["session"]["result"]
    check(
        results,
        6,
        "deterministic MCQ grade -> EVALUATED (0 evaluation requests)",
        graded["session"]["state"] == "EVALUATED"
        and result["passed"]
        and result["evaluator_type"] == "DETERMINISTIC"
        and len(provider.calls) == calls_before,
        {"status": graded["session"]["status"], "score": result["score"]},
    )

    # 7. Exactly one VERIFICATION EvidenceEvent with complete provenance.
    evidence = one(
        pool,
        """
        select e.id, e.source_id, e.actor::text, e.evidence_type::text, e.independence,
               e.grading_confidence, e.strength, e.model_run_ids, i.id, s.id, s.recommendation_id,
               s.course_id, e.attribution_id, e.mapping_id, e.segment_id, e.raw_message_ids,
               (select count(*) from public.model_runs m where m.id = any(e.model_run_ids))
          from public.evidence_events e
          join public.verification_results r on r.id = e.source_id
          join public.verification_items i on i.id = r.item_id
          join public.verification_sessions s on s.id = i.session_id
         where e.learner_id = %s and e.source_type = 'VERIFICATION'
        """,
        a_id,
    )
    check(
        results,
        7,
        "VERIFICATION EvidenceEvent with complete provenance",
        str(evidence[1]) == result["id"]
        and evidence[2:6] == ("STUDENT", "VERIFICATION", 1.0, 1.0)
        and str(evidence[9]) == session
        and str(evidence[10]) == rec["id"]
        and evidence[12:16] == (None, None, None, [])
        and evidence[16] == len(evidence[7]) == 1,
        {
            "evidence": str(evidence[0]),
            "strength": evidence[6],
            "model_runs": evidence[16],
            "chain": "evidence -> result -> item -> session -> recommendation/course/skill",
        },
    )

    # 8-10. Ledger recompute, debt, recommendation refresh.
    after = ledger_row(api, skill)
    detail = api.get(f"/v1/skills/{skill}")
    check(
        results,
        8,
        "ledger recomputed -> VERIFIED (all gates met)",
        after["mastery_state"] == "VERIFIED"
        and after["support"] >= 4.0
        and after["mastery_mean"] >= 0.8
        and detail["mastery"]["explanation_code"] == "RECENT_VERIFICATION",
        {
            "state": f"{before['mastery_state']} -> {after['mastery_state']}",
            "mean": round(after["mastery_mean"], 3),
            "support": round(after["support"], 3),
            "ledger_version": f"{before['ledger_version']} -> {after['ledger_version']}",
        },
    )
    check(
        results,
        9,
        "debt verification factor drops to recently-passed",
        detail["debt"]["verification"] == "RECENTLY_PASSED"
        and not after["debt_actionable"]
        and after["debt_score"] < before["debt_score"] / 3,
        {
            "debt": f"{before['debt_score']:.2f} -> {after['debt_score']:.2f}",
            "factor": next(
                f["value"] for f in detail["debt"]["factors"] if f["code"] == "VERIFICATION"
            ),
        },
    )
    old = one(pool, "select state::text from public.recommendations where id = %s", rec["id"])[0]
    check(
        results,
        10,
        "recommendation refreshed",
        old == "COMPLETED" and detail["recommendation"]["reason_code"] == "RECENTLY_VERIFIED",
        {
            "verify": old,
            "now": (detail["recommendation"]["type"], detail["recommendation"]["reason_code"]),
        },
    )

    # 11. Replay creates nothing and makes no model request.
    snapshot = session_counts(pool, a_id)
    calls = len(provider.calls)
    runs = one(pool, "select count(*) from public.model_runs")[0]
    api.get("/v1/verifications")
    with pool.connection() as conn:
        conn.execute(
            "update public.processing_jobs set state = 'PENDING', available_at = now() "
            "where entity_id = %s",
            (session,),
        )
    worker.drain()
    check(
        results,
        11,
        "replay: 0 new rows, 0 model requests",
        session_counts(pool, a_id) == snapshot
        and len(provider.calls) == calls
        and one(pool, "select count(*) from public.model_runs")[0] == runs,
        snapshot,
    )

    # 12. B: an abandoned check - no result, no evidence, no penalty.
    b_id, b_token, *_ = learners["b"]
    b_api = Http(args.api, b_token)
    b_skill = str(seeds["b"].skills["left_join"])
    b_before = ledger_row(b_api, b_skill)
    b_session = open_check(b_api, pool, worker, b_id)
    b_api.call("POST", f"/v1/verifications/{b_session}/start")
    stopped = b_api.call("POST", f"/v1/verifications/{b_session}/abandon")
    worker.drain()
    b_after = ledger_row(b_api, b_skill)
    check(
        results,
        12,
        "abandoned session: no result, no evidence, no mastery penalty",
        stopped[0] == 200
        and stopped[1]["session"]["status"] == "ABANDONED"
        and session_counts(pool, b_id) == {"sessions": 1, "items": 1, "results": 0, "evidence": 0}
        and (b_after["mastery_state"], b_after["alpha"], b_after["beta"])
        == (b_before["mastery_state"], b_before["alpha"], b_before["beta"]),
        session_counts(pool, b_id),
    )

    # 13. C: a wrong answer is negative evidence; debt stays derived from delegation only.
    c_id, c_token, *_ = learners["c"]
    c_api = Http(args.api, c_token)
    c_skill = str(seeds["c"].skills["left_join"])
    c_before = ledger_row(c_api, c_skill)
    c_session = open_check(c_api, pool, worker, c_id)
    c_api.call("POST", f"/v1/verifications/{c_session}/start")
    c_api.call(
        "POST",
        f"/v1/verifications/{c_session}/submit",
        {"selected": WRONG},
        {"Idempotency-Key": f"p6acc-{tag}-c"},
    )
    worker.drain()
    c_after = ledger_row(c_api, c_skill)
    c_detail = c_api.get(f"/v1/skills/{c_skill}")
    verification_items = [
        t for t in c_detail["evidence"] if t["event"]["source_type"] == "VERIFICATION"
    ]
    check(
        results,
        13,
        "failed verification: negative evidence, factor 1.0, delegation unchanged",
        c_detail["mastery"]["state"] != "VERIFIED"
        and c_after["mastery_mean"] < c_before["mastery_mean"]
        and verification_items
        and verification_items[0]["event"]["outcome"] == 0
        and not verification_items[0]["counts_toward_debt"]
        and c_detail["debt"]["verification"] == "FAILED"
        and c_after["recent_delegation_count"] == c_before["recent_delegation_count"],
        {
            "state": c_detail["mastery"]["state"],
            "mean": f"{c_before['mastery_mean']:.3f} -> {c_after['mastery_mean']:.3f}",
            "delegations": c_after["recent_delegation_count"],
        },
    )
    # ... and on a skill without delegation a failed verification creates no debt at all.
    sorting = seeds["c"].skills["sorting"]
    with pool.connection() as conn:
        policy = load_policy(conn)
        result_id = graded_verification(conn, UUID(c_id), sorting, outcome_signal="INCORRECT")
        conn.commit()
        (sub_session,) = conn.execute(
            "select session_id from public.verification_results where id = %s", (result_id,)
        ).fetchone()
        persist.repair_grading(conn, persist.load_submission(conn, sub_session), policy=policy)
        from app.intelligence.mastery.ledger import recompute_ledger

        recompute_ledger(conn, UUID(c_id), [sorting], policy=policy)
    sorting_row = ledger_row(c_api, str(sorting))
    check(
        results,
        14,
        "failed verification without delegation fabricates no debt",
        not sorting_row["debt_eligible"] and sorting_row["debt_score"] == 0,
        {"debt_eligible": sorting_row["debt_eligible"], "debt": sorting_row["debt_score"]},
    )

    # 15. Zero real model calls: every model run of this acceptance is the scripted fake.
    providers = [r[0] for r in _runs_since(pool, started)]
    check(
        results,
        15,
        "0 real model calls (all model_runs are the scripted fake)",
        providers and set(providers) == {"fake"},
        {"model_runs": len(providers), "providers": sorted(set(providers))},
    )

    passed = all(r["passed"] for r in results)
    evidence_file = {
        "passed": passed,
        "backend": {k: health[k] for k in ("service", "version", "environment")},
        "learners": {label: learners[label][0] for label in learners},
        "sessions": {"a": session, "b": b_session, "c": c_session},
        "results": results,
    }
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(evidence_file, indent=2), encoding="utf-8")
    if args.ui_credentials:
        ui = learners["ui"]
        args.ui_credentials.parent.mkdir(parents=True, exist_ok=True)
        expect = args.ui_credentials.with_suffix(".expect.json")
        expect.write_text(
            json.dumps(
                {
                    "skill_id": str(seeds["ui"].skills["left_join"]),
                    "skill_name": f"P6ui{tag} Choosing LEFT JOIN for optional matches",
                    "state_before": "DEVELOPING",
                    "answer": {"selected": CORRECT},
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        args.ui_credentials.write_text(
            f"P6_ACCEPTANCE_EMAIL={ui[2]}\nP6_ACCEPTANCE_PASSWORD={ui[3]}\n"
            f"P6_ACCEPTANCE_EXPECT={expect.resolve().as_posix()}\n",
            encoding="utf-8",
        )
    pool.close()
    print("P6 LOCAL ACCEPTANCE:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


def _runs_since(pool, since: datetime):
    with pool.connection() as conn:
        return conn.execute(
            "select provider from public.model_runs where created_at >= %s", (since,)
        ).fetchall()


def worker_loop(args) -> int:
    """The fake-provider worker for the browser walkthrough (generation and grading)."""
    pool = create_pool(args.database_url, max_size=4)
    worker = fake_worker(pool, scripted_provider())
    stop = threading.Event()
    thread = threading.Thread(target=worker.run_forever, args=(stop, 1.0), daemon=True)
    thread.start()
    print(f"fake worker running for {args.seconds}s (no real model)")
    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    stop.set()
    thread.join(timeout=10)
    print(f"fake worker stopped: {worker.stats}")
    pool.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="SkillMirror P6 local acceptance (fake provider)")
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--api", required=True)
    run_parser.add_argument("--supabase-url", required=True)
    run_parser.add_argument("--anon-key", required=True)
    run_parser.add_argument("--database-url", required=True)
    run_parser.add_argument("--out", type=Path, default=None)
    run_parser.add_argument("--ui-credentials", type=Path, default=None)
    worker_parser = sub.add_parser("worker")
    worker_parser.add_argument("--database-url", required=True)
    worker_parser.add_argument("--seconds", type=int, default=600)
    args = parser.parse_args()
    return run(args) if args.command == "run" else worker_loop(args)


if __name__ == "__main__":
    raise SystemExit(main())
