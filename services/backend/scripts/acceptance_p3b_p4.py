"""Real P3B + P4 acceptance (manual gate; never run in CI).

Needs: migrations 0001-0006 on the target database, services/backend/.env with DATABASE_URL
+ GEMINI_API_KEY, and the backend running locally (uvicorn :8000; its in-process worker
processes the turn). Reuses an existing READY course (its graph and embeddings), so no
graph is generated:

    cd services/backend
    .venv/Scripts/python scripts/acceptance_p3b_p4.py --course-id <uuid> --out ../../test-results/p3b-p4-acceptance/evidence.json

`--drive-worker` drains the worker in this process instead (no uvicorn needed).

One new learning turn is ingested through the ingestion service (the course owner's
password is not stored), in which the learner writes their own loop and asks the AI for
the next step. It then waits for PROCESS_RAW_MESSAGE and prints the whole chain:

    raw_messages -> activity_segments -> mapping_decisions -> skill_mappings (ACCEPTED)
      -> attributions -> evidence_events -> model_runs -> skill_ledger (mastery + debt)

Expected provider requests (ADR 0004/0005): 1 query embedding, 1 TURN_ANALYSIS, 1
SKILL_ATTRIBUTION (+1 only for a genuinely needed repair or adjudication). The budget is
printed before and after; a replay of the job is run in-process and must make 0 requests
and write nothing. No model output is forced. No secret is printed or written.
"""

import argparse
import json
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings  # noqa: E402
from app.db.pool import create_pool  # noqa: E402
from app.ingestion.fingerprint import content_hash  # noqa: E402
from app.ingestion.models import EventBatchClientInfo, RawActivityEnvelope  # noqa: E402
from app.ingestion.service import ingest_batch  # noqa: E402
from app.intelligence.mastery.ledger import read_ledger  # noqa: E402
from app.intelligence.policy import load_policy  # noqa: E402
from app.intelligence.processing.pipeline import process_raw_message_job  # noqa: E402
from app.jobs.queue import JOB_PROCESS_RAW_MESSAGE, ClaimedJob  # noqa: E402
from app.jobs.worker import build_worker  # noqa: E402
from app.model_gateway import build_gateway  # noqa: E402

USER_TURN = (
    "I wrote this myself to print every name in my list: `for name in names: print(name)`. "
    "Now can you write the version that also prints each name's position for me?"
)
ASSISTANT_TURN = (
    "Your loop is correct: it visits every item of the list. For the position, use enumerate: "
    "`for i, name in enumerate(names): print(i, name)`. It yields the index and the item together."
)


def budget(gateway) -> list[dict]:
    return [
        {"model": b.model, "used": b.used, "limit": b.limit, "reserve": b.reserve,
         "available": b.available}
        for b in gateway.budget_status()
    ]  # fmt: skip


def rows(pool, sql: str, *params) -> list[tuple]:
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchall()


def main() -> int:
    parser = argparse.ArgumentParser(description="SkillMirror P3B + P4 real acceptance")
    parser.add_argument("--course-id", required=True, help="an existing READY course to reuse")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--drive-worker", action="store_true", help="drain the worker here")
    args = parser.parse_args()

    settings = get_settings()
    if settings.database_url is None or settings.gemini_api_key is None:
        raise SystemExit("DATABASE_URL and GEMINI_API_KEY must be set in services/backend/.env")
    pool = create_pool(settings.database_url.get_secret_value())
    gateway = build_gateway(settings, pool)  # fails fast without migration 0004
    with pool.connection() as conn:
        policy = load_policy(conn)  # fails fast without migrations 0005/0006
        course = conn.execute(
            "select owner_id, name, graph_status::text from public.courses where id = %s",
            (args.course_id,),
        ).fetchone()
    if course is None or course[2] != "READY":
        raise SystemExit(f"course {args.course_id} is missing or not READY")
    learner = course[0]
    routing = gateway.routing
    evidence: dict = {
        "started_at": datetime.now(UTC).isoformat(),
        "course_id": args.course_id,
        "learner_id": str(learner),
        "model_policy": {
            "routing": routing.name,
            "default_model": routing.default_model,
            "routine_model": routing.routine_model,
            "budget_before": budget(gateway),
        },
    }
    print(f"[0] model policy {routing.name}: default={routing.default_model} "
          f"routine={routing.routine_model or routing.default_model}")  # fmt: skip
    for b in evidence["model_policy"]["budget_before"]:
        print(f"    budget {b['model']}: {b['used']}/{b['limit']} used, reserve {b['reserve']}")
    print(f"[1] course {args.course_id} ({course[1]}) of learner {learner}; policy loaded")

    # [2] One new captured turn (new conversation), through the production ingestion service.
    conversation = f"acceptance-p3b-{uuid.uuid4().hex[:10]}"
    user_ext, assistant_ext = f"u-{uuid.uuid4().hex[:12]}", f"a-{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC).isoformat()

    def envelope(ext, parent, index, role, text):
        return RawActivityEnvelope.model_validate(
            {
                "event_id": str(uuid.uuid4()),
                "schema_version": 1,
                "learner_id": str(learner),
                "source_provider": "chatgpt",
                "source_method": "browser_extension",
                "external_conversation_id": conversation,
                "external_message_id": ext,
                "external_parent_message_id": parent,
                "message_index": index,
                "role": role,
                "content_text": text,
                "content_format": "text",
                "occurred_at": None,
                "captured_at": now,
                "provider_model": None,
                "revision_index": 0,
                "attachment_metadata": [],
                "context_incomplete": False,
                "active_course_id": args.course_id,
                "content_hash": content_hash(text),
                "client_event_id": f"chatgpt:{ext}:r0",
            }
        )

    with pool.connection() as conn:
        stored = ingest_batch(
            conn,
            learner,
            [envelope(user_ext, None, 0, "user", USER_TURN),
             envelope(assistant_ext, user_ext, 1, "assistant", ASSISTANT_TURN)],
            EventBatchClientInfo(extension_version="0.2.0", adapter_version="acceptance-p3b"),
        )  # fmt: skip
    user_id, assistant_id = stored[0].raw_message_id, stored[1].raw_message_id
    evidence["raw_messages"] = {"user": str(user_id), "assistant": str(assistant_id)}
    print(f"[2] ingested turn: user {user_id}, assistant {assistant_id}")

    # [3] Wait for PROCESS_RAW_MESSAGE (the backend's worker, or this process).
    worker = build_worker(pool, gateway, batch_size=4) if args.drive_worker else None
    deadline = time.monotonic() + args.timeout
    while True:
        if worker:
            worker.drain(max_rounds=5)
        (state,) = rows(
            pool,
            "select state::text, attempts, outcome, id from public.processing_jobs "
            "where entity_id = %s and job_type = %s",
            assistant_id,
            JOB_PROCESS_RAW_MESSAGE,
        )
        print(f"    job {state[3]}: {state[0]} attempts={state[1]} outcome={state[2]}")
        if state[0] in ("COMPLETED", "FAILED"):
            break
        if time.monotonic() > deadline:
            raise SystemExit("timed out waiting for the job (backpressure? see outcome)")
        time.sleep(10)
    job_id = state[3]
    evidence["job"] = {"id": str(job_id), "state": state[0], "outcome": state[2]}
    if state[0] != "COMPLETED":
        raise SystemExit(f"job failed: {state}")

    runs = rows(
        pool,
        "select id, task_type, model, prompt_version, status::text, attempt, total_tokens, "
        "cache_source_run_id is not null from public.model_runs where trace_id = %s order by created_at",
        f"job:{job_id}",
    )
    print("[3] model runs of the job:")
    for r in runs:
        print(
            f"    {r[1]:<18} {r[2]:<24} {r[3]:<22} {r[4]:<14} attempt={r[5]} tokens={r[6]} cache={r[7]}"
        )
    evidence["model_runs"] = [
        {"id": str(r[0]), "task": r[1], "model": r[2], "prompt_version": r[3], "status": r[4],
         "attempt": r[5], "cache_hit": r[7]}
        for r in runs
    ]  # fmt: skip

    mappings = rows(
        pool,
        """select m.id, n.canonical_name, m.status::text, m.confidence, m.status_reason, m.evidence_span,
                  s.route::text, d.outcome::text
             from public.activity_segments s
             join public.mapping_decisions d on d.segment_id = s.id
             join public.skill_mappings m on m.decision_id = d.id
             join public.skill_nodes n on n.id = m.skill_id
            where s.anchor_message_id = %s order by m.confidence desc""",
        user_id,
    )
    print("[4] P3A mapping:")
    for m in mappings:
        print(f"    {m[1]}: {m[2]} {m[3]:.2f} ({m[4]}) span={m[5]!r}")
    attributions = rows(
        pool,
        """select a.id, n.canonical_name, a.status::text, a.actor::text, a.confidence,
                  a.proposed_evidence_type, a.outcome_signal::text, a.rationale_code, a.evidence_decision,
                  a.student_span, a.ai_span, r.task_type, r.prompt_version, r.model
             from public.attributions a join public.skill_nodes n on n.id = a.skill_id
             left join public.model_runs r on r.id = a.model_run_id
             join public.activity_segments s on s.id = a.segment_id
            where s.anchor_message_id = %s""",
        user_id,
    )
    print("[5] P3B attribution (one call per mapped segment):")
    for a in attributions:
        print(f"    {a[1]}: {a[2]} actor={a[3]} conf={a[4]} type={a[5]} outcome={a[6]} "
              f"reason={a[7]} -> {a[8]}\n      student={a[9]!r}\n      ai={a[10]!r}")  # fmt: skip
    events = rows(
        pool,
        """select e.id, n.canonical_name, e.evidence_type::text, e.actor::text, e.outcome_signal::text,
                  e.outcome, e.strength, e.base_weight, e.difficulty_multiplier, e.independence,
                  e.evidence_confidence, e.qualification_reason, e.raw_message_ids, e.model_run_ids,
                  s.source_message_ids, e.skill_id
             from public.evidence_events e join public.skill_nodes n on n.id = e.skill_id
             join public.activity_segments s on s.id = e.segment_id
            where s.anchor_message_id = %s""",
        user_id,
    )
    print("[6] EvidenceEvents (deterministic strength):")
    for e in events:
        print(f"    {e[1]}: {e[2]} actor={e[3]} {e[4]} outcome={e[5]} strength={e[6]:.4f} "
              f"(= {e[7]} x {e[8]} x {e[9]} x {e[10]:.3f}) [{e[11]}]")  # fmt: skip
        assert e[12] == e[14] == [user_id, assistant_id], "raw provenance"
        assert set(e[13]) <= {r[0] for r in runs}, "model run provenance"
        if e[2] in ("EXPOSURE", "OBSERVATION"):
            assert e[6] == 0 and e[5] is None, "exposure/observation guard"

    with pool.connection() as conn:
        ledger = read_ledger(conn, learner, policy=policy, course_id=uuid.UUID(args.course_id))
    touched = {e[15] for e in events}
    print("[7] P4 ledger (GET /v1/ledger read model):")
    ledger_rows = [s for s in ledger.skills if s.skill_id in touched]
    for s in ledger_rows:
        print(f"    {s.canonical_name}: {s.mastery_state} mean={s.mastery_mean} alpha={s.alpha:.4f} "
              f"beta={s.beta:.4f} support={s.support:.4f} | debt eligible={s.debt_eligible} "
              f"score={s.debt_score} delegations={s.recent_delegation_count}")  # fmt: skip
    ai_used = [e for e in events if e[3] in ("AI", "SHARED")]
    no_debt = [s for s in ledger_rows if not s.debt_eligible and s.debt_score == 0]
    print(f"    AI/SHARED evidence events: {len(ai_used)}; skills with no debt: {len(no_debt)}")

    # [8] Replay: the same job again must make no provider request and write nothing.
    before = rows(
        pool,
        "select (select count(*) from public.model_runs where trace_id = %s), "
        "(select count(*) from public.attributions where learner_id = %s), "
        "(select count(*) from public.evidence_events where learner_id = %s)",
        f"job:{job_id}",
        learner,
        learner,
    )
    job = ClaimedJob(job_id, JOB_PROCESS_RAW_MESSAGE, "raw_message", assistant_id, learner, 1, 3)
    replay = process_raw_message_job(pool, gateway, job)
    after = rows(
        pool,
        "select (select count(*) from public.model_runs where trace_id = %s), "
        "(select count(*) from public.attributions where learner_id = %s), "
        "(select count(*) from public.evidence_events where learner_id = %s)",
        f"job:{job_id}",
        learner,
        learner,
    )
    print(f"[8] replay -> {replay.outcome}; runs/attributions/evidence {before[0]} -> {after[0]}")
    assert before == after, "replay must not duplicate anything"

    spent = budget(gateway)
    print("[9] budget after:")
    for b in spent:
        print(f"    budget {b['model']}: {b['used']}/{b['limit']} used, reserve {b['reserve']}")
    evidence.update(
        {
            "mappings": [{"skill": m[1], "status": m[2], "confidence": m[3], "reason": m[4]} for m in mappings],
            "attributions": [
                {"id": str(a[0]), "skill": a[1], "status": a[2], "actor": a[3], "confidence": a[4],
                 "evidence_type": a[5], "outcome_signal": a[6], "reason": a[7], "decision": a[8]}
                for a in attributions
            ],
            "evidence_events": [
                {"id": str(e[0]), "skill": e[1], "type": e[2], "actor": e[3], "outcome_signal": e[4],
                 "outcome": e[5], "strength": e[6], "confidence": e[10], "reason": e[11]}
                for e in events
            ],
            "ledger": [s.model_dump(mode="json") for s in ledger_rows],
            "replay": {"outcome": replay.outcome, "unchanged": before == after},
            "budget_after": spent,
            "finished_at": datetime.now(UTC).isoformat(),
        }
    )  # fmt: skip
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")
        print(f"evidence written to {args.out}")
    pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
