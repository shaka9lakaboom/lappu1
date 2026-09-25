"""Real P2 + P3A acceptance (manual gate; never run in CI).

Needs: the backend running locally with DATABASE_URL + GEMINI_API_KEY (so the worker runs),
migration 0003 on the target database, and apps/web/.env.local (public Supabase URL + anon key).

    cd services/backend
    .venv/Scripts/python scripts/acceptance_p2_p3a.py --out ../../apps/extension/test-results/p2-p3a-evidence.json

Resume an earlier run (reuse its course instead of generating a new graph; the
throwaway learner's password is never stored, so the turn is ingested through the same
ingestion service the API handler calls):

    .venv/Scripts/python scripts/acceptance_p2_p3a.py --resume-course <course uuid>

Steps: sign up a fresh learner (public Auth API) -> POST /v1/courses -> wait for the
bootstrap job (graph READY) -> inspect skills + embeddings -> real retrieval query ->
send a real learning turn through POST /v1/events/batch -> wait for processing ->
print the provenance chain (segment -> decision -> mappings -> model_runs -> raw_messages).
No model output is forced. No secret is printed or written.

Provider-request budget (ADR 0004, combined turn analysis): a new course costs 1 graph
request + 1-2 embedding requests; the learning turn 1 query embedding + 1 generation
request (+1 adjudication only if a mapping lands in 0.65-0.79). The retrieval probe is an
embedding only. `--rerank-probe` adds one staged rerank request. The script prints the
day's budget first and the requests actually spent at the end; the gateway never
touches the configured reserve.
"""

import argparse
import json
import secrets
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings  # noqa: E402
from app.courses.service import get_course, get_course_skills  # noqa: E402
from app.db.pool import create_pool  # noqa: E402
from app.ingestion.fingerprint import content_hash  # noqa: E402
from app.ingestion.models import EventBatchClientInfo, RawActivityEnvelope  # noqa: E402
from app.ingestion.service import ingest_batch  # noqa: E402
from app.intelligence.policy import load_policy  # noqa: E402
from app.intelligence.retrieval.engine import (  # noqa: E402
    QUERY_INPUT_VERSION,
    QUERY_TASK_TYPE,
    fetch_raw_candidates,
    retrieve_candidates,
    to_contract,
)
from app.intelligence.retrieval.scoring import score_candidates  # noqa: E402
from app.model_gateway import RunContext, build_gateway  # noqa: E402

WEB_ENV = BACKEND.parents[1] / "apps" / "web" / ".env.local"
COURSE = {"name": "Introduction to Python Programming", "level": "Beginner"}
QUERY = "How do I loop over a list and print each item with its index?"
USER_TURN = (
    "I wrote `for i in range(len(names)): print(i, names[i])` to print each name with its "
    "position. Is there a cleaner way to loop over a list and get the index too?"
)
ASSISTANT_TURN = (
    "Yes - use enumerate: `for i, name in enumerate(names): print(i, name)`. It yields the "
    "index and the item together, so you don't need range(len(...))."
)


def read_web_env() -> dict[str, str]:
    values = {}
    for line in WEB_ENV.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"')
    return values


def wait(label: str, check, timeout: float, every: float = 5.0):
    deadline = time.monotonic() + timeout
    while True:
        result = check()
        if result is not None:
            return result
        if time.monotonic() > deadline:
            raise SystemExit(f"timed out waiting for {label}")
        time.sleep(every)


def main() -> int:
    parser = argparse.ArgumentParser(description="SkillMirror P2 + P3A real acceptance")
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument(
        "--rerank-probe",
        action="store_true",
        help="also rerank the probe query (one extra generation call); by default the "
        "rerank is verified on the real turn only, to save free-tier quota",
    )
    parser.add_argument(
        "--no-repeat",
        action="store_true",
        help="skip re-ingesting the identical turn (the cache check; 0 provider requests)",
    )
    parser.add_argument(
        "--resume-course",
        default=None,
        help="reuse an existing course (and its owner) from an earlier run",
    )
    args = parser.parse_args()

    settings = get_settings()
    if settings.database_url is None or settings.gemini_api_key is None:
        raise SystemExit("DATABASE_URL and GEMINI_API_KEY must be set in services/backend/.env")
    web = read_web_env()
    supabase_url = web["NEXT_PUBLIC_SUPABASE_URL"].rstrip("/")
    anon = web["NEXT_PUBLIC_SUPABASE_ANON_KEY"]
    pool = create_pool(settings.database_url.get_secret_value())
    started_at = datetime.now(UTC)
    evidence: dict = {"started_at": started_at.isoformat(), "api": args.api}
    http = httpx.Client(timeout=30)

    # Fails fast (ModelRunsSchemaError) if migration 0004 is missing on the target database.
    gateway = build_gateway(settings, pool)
    routing = gateway.routing
    budget = [
        {"model": b.model, "used": b.used, "limit": b.limit, "reserve": b.reserve,
         "available": b.available}
        for b in gateway.budget_status()
    ]  # fmt: skip
    print(
        f"[0] model policy {routing.name}: default={routing.default_model} "
        f"routine={routing.routine_model or routing.default_model} "
        f"turn_analysis={settings.turn_analysis_mode}"
    )
    for b in budget:
        print(
            f"    budget {b['model']}: {b['used']}/{b['limit']} used today, reserve "
            f"{b['reserve']}, {b['available']} available"
        )
    evidence["model_policy"] = {
        "routing": routing.name,
        "default_model": routing.default_model,
        "routine_model": routing.routine_model,
        "turn_analysis_mode": settings.turn_analysis_mode,
        "budget_before": budget,
    }

    health = http.get(f"{args.api}/health").json()
    print(f"[1] backend {health['service']} {health['version']} ({health['environment']})")

    token = None
    if args.resume_course:
        course_id = args.resume_course
        with pool.connection() as conn:
            row = conn.execute(
                "select owner_id::text from public.courses where id = %s", (course_id,)
            ).fetchone()
        if row is None:
            raise SystemExit(f"course {course_id} not found")
        learner = row[0]
        evidence["learner"] = {"id": learner, "resumed": True}
        print(f"[2] resuming course {course_id} of learner {learner}")
        print("[3] (course created in the earlier run)")

        def course_done():
            with pool.connection() as conn:
                course = get_course(conn, uuid.UUID(learner), uuid.UUID(course_id))
            print(f"    graph_status={course.graph_status} job={course.bootstrap_job_state}")
            return course if course.graph_status in ("READY", "FAILED") else None

        course = wait("course bootstrap", course_done, args.timeout)
        if course.graph_status != "READY":
            raise SystemExit(f"bootstrap failed: {course.graph_error}")
        with pool.connection() as conn:
            graph = get_course_skills(conn, uuid.UUID(learner), uuid.UUID(course_id)).model_dump(
                mode="json"
            )
    else:
        email = f"skillmirror-p2-{int(time.time())}@mailinator.com"
        signup = http.post(
            f"{supabase_url}/auth/v1/signup",
            headers={"apikey": anon, "Content-Type": "application/json"},
            json={"email": email, "password": secrets.token_urlsafe(18)},
        )
        signup.raise_for_status()
        session = signup.json()
        token = session.get("access_token")
        if not token:
            raise SystemExit("signup returned no session (is email auto-confirm on?)")
        learner = session["user"]["id"]
        auth = {"Authorization": f"Bearer {token}"}
        evidence["learner"] = {"email": email, "id": learner}
        print(f"[2] learner {email} ({learner})")

        created = http.post(
            f"{args.api}/v1/courses",
            headers={**auth, "Idempotency-Key": f"acceptance-{uuid.uuid4().hex}"},
            json=COURSE,
        )
        assert created.status_code == 201, created.text
        course_id = created.json()["course"]["id"]
        with pool.connection() as conn:
            runs_at_create = conn.execute(
                "select count(*) from public.model_runs where course_id = %s", (course_id,)
            ).fetchone()[0]
        print(f"[3] course {course_id} created (201); model runs during request: {runs_at_create}")

        def course_done():
            course = http.get(f"{args.api}/v1/courses/{course_id}", headers=auth).json()
            print(f"    graph_status={course['graph_status']} job={course['bootstrap_job_state']}")
            return course if course["graph_status"] in ("READY", "FAILED") else None

        course = wait("course bootstrap", course_done, args.timeout)
        if course["graph_status"] != "READY":
            raise SystemExit(f"bootstrap failed: {course['graph_error']}")
        graph = http.get(f"{args.api}/v1/courses/{course_id}/skills", headers=auth).json()
    skills = [s for s in graph["skills"] if s["skill"]["node_kind"] in ("SKILL", "SUBSKILL")]
    topics = [s for s in graph["skills"] if s["skill"]["node_kind"] == "TOPIC"]
    with pool.connection() as conn:
        embedded = conn.execute(
            """select count(*), min(extensions.vector_dims(e.embedding)), max(e.model)
                 from public.skill_embeddings e join public.course_skills cs on cs.skill_id = e.skill_id
                where cs.course_id = %s""",
            (course_id,),
        ).fetchone()
        bootstrap_runs = conn.execute(
            "select task_type, model, prompt_version, status::text, total_tokens, latency_ms "
            "from public.model_runs where course_id = %s order by created_at",
            (course_id,),
        ).fetchall()
    print(
        f"[4] graph v{graph['graph_version']}: {len(skills)} skills, {len(topics)} topics, "
        f"{len(graph['edges'])} edges; embeddings {embedded[0]} x {embedded[1]}d ({embedded[2]})"
    )
    for s in skills[:12]:
        print(f"    - {s['skill']['canonical_name']}: {s['skill']['description']}")
    evidence["course"] = {
        "id": course_id,
        "graph_version": graph["graph_version"],
        "skills": [
            {
                "name": s["skill"]["canonical_name"],
                "description": s["skill"]["description"],
                "aliases": s["skill"]["aliases"],
                "importance": s["importance"],
            }
            for s in skills
        ],
        "topics": [t["skill"]["canonical_name"] for t in topics],
        "edges": len(graph["edges"]),
        "embeddings": {"count": embedded[0], "dimension": embedded[1], "model": embedded[2]},
        "model_runs": [list(r) for r in bootstrap_runs],
    }

    probe_ctx = RunContext(trace_id=f"acceptance:retrieval:{course_id}")
    with pool.connection() as conn:
        policy = load_policy(conn)
        if args.rerank_probe:
            retrieval = retrieve_candidates(
                conn,
                gateway,
                query_text=QUERY,
                course_ids=[uuid.UUID(course_id)],
                course_context="Introduction to Python Programming (Beginner)",
                policy=policy.retrieval,
                context=probe_ctx,
            )
            pool_candidates = list(retrieval.candidates)
        else:
            # Both retrieval channels + exact scoring; no generation call (embedding only).
            embedded = gateway.embed(
                texts=[QUERY],
                input_type="query",
                prompt_version=QUERY_INPUT_VERSION,
                task_type=QUERY_TASK_TYPE,
                context=probe_ctx,
            )
            raw = fetch_raw_candidates(
                conn,
                query_text=QUERY,
                query_vector=embedded.vectors[0],
                course_ids=[uuid.UUID(course_id)],
                embedding_model=embedded.model,
                channel_limit=policy.retrieval.channel_limit,
            )
            pool_candidates = to_contract(
                score_candidates(raw, policy.retrieval.weights, policy.retrieval.pool_size)
            )
    lexical_hits = sum(1 for c in pool_candidates if c.lexical_score > 0)
    print(
        f"[5] retrieval for {QUERY!r}: pool {len(pool_candidates)} "
        f"(lexical matches {lexical_hits}, all scored by pgvector cosine)"
    )
    for c in pool_candidates[:10]:
        print(
            f"    {c.rank:>2} {c.canonical_name} score={c.candidate_score:.3f} "
            f"sem={c.semantic_similarity:.3f} lex={c.lexical_score:.3f} prior={c.course_context_prior:.2f}"
        )
    evidence["retrieval"] = {
        "query": QUERY,
        "pool": len(pool_candidates),
        "lexical_matches": lexical_hits,
        "top10": [c.model_dump(mode="json") for c in pool_candidates[:10]],
        "reranked_in_probe": bool(args.rerank_probe),
    }

    def ingest_turn() -> tuple[list[str], str]:
        """The acceptance turn, in a new conversation each time (so an identical unit)."""
        conversation = f"acceptance-{uuid.uuid4().hex[:12]}"
        user_ext, assistant_ext = f"u-{uuid.uuid4().hex[:12]}", f"a-{uuid.uuid4().hex[:12]}"
        now = datetime.now(UTC).isoformat()

        def envelope(ext, parent, index, role, text):
            return {
                "event_id": str(uuid.uuid4()),
                "schema_version": 1,
                "learner_id": learner,
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
                "active_course_id": course_id,
                "content_hash": content_hash(text),
                "client_event_id": f"chatgpt:{ext}:r0",
            }

        batch = {
            "client": {"extension_version": "0.2.0", "adapter_version": "chatgpt-1"},
            "events": [
                envelope(user_ext, None, 0, "user", USER_TURN),
                envelope(assistant_ext, user_ext, 1, "assistant", ASSISTANT_TURN),
            ],
        }
        if token:
            ingested = http.post(
                f"{args.api}/v1/events/batch",
                headers={"Authorization": f"Bearer {token}"},
                json=batch,
            )
            ingested.raise_for_status()
            return [
                r["raw_message_id"] for r in ingested.json()["results"]
            ], "POST /v1/events/batch"
        with pool.connection() as conn:
            stored = ingest_batch(
                conn,
                uuid.UUID(learner),
                [RawActivityEnvelope.model_validate(e) for e in batch["events"]],
                EventBatchClientInfo.model_validate(batch["client"]),
            )
        return [str(r.raw_message_id) for r in stored], "ingestion service"

    def jobs_done(ids):
        with pool.connection() as conn:
            rows = conn.execute(
                "select entity_id::text, state::text, outcome, attempts, last_error "
                "from public.processing_jobs where entity_id = any(%s::uuid[])",
                (ids,),
            ).fetchall()
        print(f"    jobs: {[(r[1], r[2]) for r in rows]}")
        return rows if rows and all(r[1] in ("COMPLETED", "FAILED") for r in rows) else None

    raw_ids, via = ingest_turn()
    print(f"[6] turn ingested via {via}: raw messages {raw_ids}")

    jobs = wait("turn processing", lambda: jobs_done(raw_ids), args.timeout)
    with pool.connection() as conn:
        segments = conn.execute(
            """select s.id::text, s.segment_index, s.route::text, s.route_reason, s.context::text,
                      s.intent::text, s.learning_relevance::text, s.relevance_confidence,
                      s.skill_bearing, s.skill_bearing_confidence, s.reason_code,
                      s.user_message_id::text, s.assistant_message_id::text,
                      s.qualification_model_run_id::text, s.text
                 from public.activity_segments s where s.anchor_message_id = %s::uuid
                order by s.segment_index""",
            (raw_ids[0],),
        ).fetchall()
        decisions = conn.execute(
            """select d.segment_id::text, d.outcome::text, d.abstain_reason,
                      jsonb_array_length(d.retrieval_candidates), cardinality(d.mapper_candidate_ids),
                      d.rerank_fallback, d.mapping_model_run_id::text, d.adjudication_model_run_id::text,
                      d.prompt_versions, d.new_skill_candidate_id::text, d.mapper_version,
                      d.rerank_model_run_id::text
                 from public.mapping_decisions d
                 join public.activity_segments s on s.id = d.segment_id
                where s.anchor_message_id = %s::uuid""",
            (raw_ids[0],),
        ).fetchall()
        mappings = conn.execute(
            """select n.canonical_name, m.status::text, m.status_reason, m.first_pass_confidence,
                      m.confidence, m.adjudicated, m.reason_code, m.evidence_span
                 from public.skill_mappings m join public.skill_nodes n on n.id = m.skill_id
                 join public.activity_segments s on s.id = m.segment_id
                where s.anchor_message_id = %s::uuid""",
            (raw_ids[0],),
        ).fetchall()
        job_ids = [
            r[0]
            for r in conn.execute(
                "select id::text from public.processing_jobs where entity_id = any(%s::uuid[])",
                (raw_ids,),
            ).fetchall()
        ]
        runs = conn.execute(
            "select trace_id, task_type, model, prompt_version, status::text, total_tokens, latency_ms, "
            "cache_source_run_id::text from public.model_runs where trace_id = any(%s) order by created_at",
            ([f"job:{j}" for j in job_ids],),
        ).fetchall()
        spent = conn.execute(
            "select model, count(*) from public.model_runs "
            "where created_at >= %s and cache_source_run_id is null group by model order by model",
            (started_at,),
        ).fetchall()
        evidence_tables = conn.execute(
            "select count(*) from pg_tables where schemaname = 'public' "
            "and tablename in ('evidence_events', 'skill_ledger', 'attributions')"
        ).fetchone()[0]
    print("[7] provenance")
    for s in segments:
        print(
            f"    segment {s[1]} route={s[2]} ({s[3]}) context={s[4]} intent={s[5]} "
            f"relevance={s[6]}@{s[7]} skill_bearing={s[8]}@{s[9]} reason={s[10]}"
        )
        print(f"      user={s[11]} assistant={s[12]} qualification_run={s[13]}")
    for d in decisions:
        print(
            f"    decision {d[1]} abstain={d[2]} pool={d[3]} mapper_candidates={d[4]} fallback={d[5]} "
            f"mapper={d[10]} one_call={d[11] == d[6]}"
        )
    for m in mappings:
        print(
            f"    mapping {m[0]}: {m[1]} ({m[2]}) first={m[3]:.2f} final={m[4]:.2f} span={m[7]!r}"
        )
    for r in runs:
        source = f"cache:{r[7]}" if r[7] else "provider"
        print(f"    model_run {r[1]} {r[2]} {r[3]} {r[4]} tokens={r[5]} ms={r[6]} {source}")
    print("[8] provider requests spent during this run (all processes, cache hits excluded)")
    for model, count in spent:
        print(f"    {model}: {count}")
    print(f"    evidence tables present: {evidence_tables} (P3A must create none)")
    evidence["turn"] = {
        "raw_message_ids": raw_ids,
        "jobs": [list(j) for j in jobs],
        "segments": [list(s) for s in segments],
        "decisions": [list(d) for d in decisions],
        "mappings": [list(m) for m in mappings],
        "model_runs": [list(r) for r in runs],
        "evidence_tables_present": evidence_tables,
    }
    evidence["provider_requests_spent"] = {model: count for model, count in spent}

    if not args.no_repeat:
        # The identical turn again, handled by the same worker process: every model call
        # must be served from the exact result cache, with zero provider requests.
        repeat_started = datetime.now(UTC)
        repeat_ids, _ = ingest_turn()
        print(f"[9] identical turn re-ingested in a new conversation: raw messages {repeat_ids}")
        wait("repeat processing", lambda: jobs_done(repeat_ids), args.timeout)
        with pool.connection() as conn:
            repeat_jobs = [
                r[0]
                for r in conn.execute(
                    "select id::text from public.processing_jobs where entity_id = any(%s::uuid[])",
                    (repeat_ids,),
                ).fetchall()
            ]
            repeat_runs = conn.execute(
                "select task_type, model, status::text, cache_source_run_id::text "
                "from public.model_runs where trace_id = any(%s) order by created_at",
                ([f"job:{j}" for j in repeat_jobs],),
            ).fetchall()
            repeat_requests = conn.execute(
                "select count(*) from public.model_runs "
                "where created_at >= %s and cache_source_run_id is null",
                (repeat_started,),
            ).fetchone()[0]
            repeat_decisions = conn.execute(
                "select d.outcome::text, d.abstain_reason from public.mapping_decisions d "
                "join public.activity_segments s on s.id = d.segment_id "
                "where s.anchor_message_id = %s::uuid",
                (repeat_ids[0],),
            ).fetchall()
        for r in repeat_runs:
            print(f"    model_run {r[0]} {r[1]} {r[2]} {'cache:' + r[3] if r[3] else 'PROVIDER'}")
        print(
            f"    provider requests during the repeat: {repeat_requests} (must be 0); "
            f"decisions {repeat_decisions} (first run: {[(d[1], d[2]) for d in decisions]})"
        )
        evidence["repeat"] = {
            "raw_message_ids": repeat_ids,
            "model_runs": [list(r) for r in repeat_runs],
            "provider_requests": repeat_requests,
            "all_cache_hits": bool(repeat_runs) and all(r[3] for r in repeat_runs),
            "decisions": [list(d) for d in repeat_decisions],
        }

    budget_after = gateway.budget_status()
    print("[10] budget after the run")
    for b in budget_after:
        print(
            f"    {b.model}: {b.used}/{b.limit} used today, reserve {b.reserve}, "
            f"{b.available} available, reserve intact: {b.used + b.reserve <= b.limit}"
        )
    evidence["model_policy"]["budget_after"] = [
        {"model": b.model, "used": b.used, "limit": b.limit, "reserve": b.reserve,
         "available": b.available, "reserve_intact": b.used + b.reserve <= b.limit}
        for b in budget_after
    ]  # fmt: skip
    evidence["finished_at"] = datetime.now(UTC).isoformat()
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")
        print(f"evidence written to {args.out}")
    pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
