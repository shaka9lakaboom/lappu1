"""Hosted E2E smoke of the merged P7 + P8 code (manual gate; never run in CI; ADR 0008 §43).

Two parts, each proved on hosted with ids only:

1. The 2026-09-25 attribution / evidence defect (learner 8afbd2c8…, skill "Writing for loops over
   ranges"): the operator recompute of the merged code (`scripts/recompute_skill.py --apply`, the
   startup sweep's own calls for that one row) must give false delegations 2 -> 1, debt 30.1 -> 0
   and no VERIFY, with activity actor == evidence actor, without touching the immutable evidence,
   the attributions or the READY verification session.
2. One disposable learner on the existing course 9440004a (no registry row) captures two turns of
   the same shape through POST /v1/events/batch (the extension's chatgpt-2 envelope): the AI writes
   a loop; the learner reuses it with their own explanation and asks the AI to check it. A worker of
   the merged code registered for PROCESS_RAW_MESSAGE only (no startup sweep, so no other learner's
   ledger is re-derived) runs the real gateway on the Flash-Lite runtime.

    inspect        read-only: migrations 0001-0009 only, no open job, the affected row (stored vs
                   the merged code), the READY session; snapshots (tables, the real P3B/P4 learner,
                   the affected learner, registry, model runs)
    readmodel      read-only, before the recompute: activity actor == evidence actor already (the
                   merged read model); nothing of the learner changed; the READY session untouched
    baseline       read-only: the whole-database snapshot right before the recompute (A8 is judged
                   against it, so phases that write in between do not count)
    (operator)     scripts/recompute_skill.py --learner <8afbd2c8…> --skill <2761328b…> [--apply]
    affected       read-only: the recomputed row, its recommendations, activity consistency over all
                   of the learner's activity, evidence / attributions and the READY session
                   untouched; vs the baseline only the ledger and recommendations changed, 0 model
                   runs and 0 registry / evidence / attribution / raw rows were created
                   (`--baseline <snapshot>` names another pre-recompute snapshot)
    prepare        the disposable learner (hosted Auth signup) + a STUDENT membership of 9440004a
    run            the two turns, each drained by the PROCESS_RAW_MESSAGE-only worker
    (browser)      apps/web/e2e/p8-smoke.spec.ts, learner test
    verify         chain, activity == evidence, delegations / debt / recommendations derived by the
                   merged code, no plan, idempotent replay (0 requests, 0 rows), requests of the run
    cleanup        Auth admin delete (the normal cascade); nothing learner-owned; shared rows,
                   the real learner and the affected learner unchanged
    (operator)     benchmark/runners/critical_gate.py record --from-report <report> --record-to <hosted>
    benchmark      the hosted rows equal the saved reports field by field; a disposable ADMIN
                   (audited ROLE_CHANGE) reads GET /v1/admin/benchmark
    (browser)      apps/web/e2e/p8-smoke.spec.ts, admin test
    admin-cleanup  delete the admin; nothing it owned remains

    cd services/backend
    # API: services/backend/.env with GEMINI_API_KEY=' ' and WORKER_ENABLED=false (port 8001)
    # run phase only (runtime overrides, never committed):
    #   GEMINI_GENERATION_MODEL=gemini-3.5-flash-lite GEMINI_ROUTINE_MODEL=gemini-3.5-flash-lite
    #   GEMINI_GENERATION_RPM=12 MODEL_QUOTA_RESERVE=25
    #   MODEL_DAILY_REQUEST_LIMITS=gemini-3.5-flash-lite=500,gemini-3.7-flash=20,gemini-3.8-flash=20
    .venv/Scripts/python scripts/smoke_p8_hosted.py <phase> [--api http://127.0.0.1:8001]

Connection values come from services/backend/.env (DATABASE_URL, SUPABASE_URL, GEMINI_API_KEY for
the run phase only), apps/web/.env.local (anon key) and apps/web/.env.e2e.local (service-role key,
cleanup only); none is printed or written. State and evidence go to test-results/p8-hosted/
(git-ignored).
"""

import argparse
import hashlib
import json
import secrets
import sys
import time
import urllib.request
import uuid
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from acceptance_p5 import Http  # noqa: E402
from acceptance_p5_hosted import Report, env_value, one, real_snapshot, registry, rows  # noqa: E402
from grant_role import change_role  # noqa: E402
from hosted_snapshot import snapshot  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.pool import create_pool  # noqa: E402
from app.experience.activity import list_activity  # noqa: E402
from app.ingestion.fingerprint import content_hash  # noqa: E402
from app.intelligence.debt.engine import compute_debt, is_delegation  # noqa: E402
from app.intelligence.evidence.engine import copied_from_ai  # noqa: E402
from app.intelligence.mastery.engine import ALGORITHM_VERSION, compute_mastery  # noqa: E402
from app.intelligence.mastery.ledger import load_records, skill_importance  # noqa: E402
from app.intelligence.policy import load_policy  # noqa: E402
from app.intelligence.processing.pipeline import process_raw_message_job  # noqa: E402
from app.jobs.queue import JOB_PROCESS_RAW_MESSAGE, ClaimedJob  # noqa: E402
from app.jobs.worker import Worker  # noqa: E402
from app.model_gateway import build_gateway  # noqa: E402

COURSE = "9440004a-a25e-4e15-94c0-17c21f6bd695"
AFFECTED_LEARNER = "8afbd2c8-c48a-49d4-9062-7ab07ac8e380"
AFFECTED_SKILL = "2761328b-f60d-4b98-9d4e-269018fc732f"  # Writing for loops over ranges
AFFECTED_RECOMMENDATION = "e8d6092f-33f0-4ca5-a4b7-e9788425c4ad"  # the false VERIFY
AFFECTED_SESSION = "3aa4481e-f4ff-4c09-a4f2-92331948c947"  # the READY check
AFFECTED_MAPPING_PREFIX = "aeac8331"  # the defect turn's mapping
EXPECTED_MIGRATIONS = [f"{n:04d}" for n in range(1, 10)]
OPERATIONAL_MODEL = "gemini-3.5-flash-lite"
MAX_GENERATION_REQUESTS = 10  # 2 turns: ~2 TURN_ANALYSIS + ~2-3 SKILL_ATTRIBUTION (+ repair)
EXTENSION_VERSION, ADAPTER_VERSION = "0.2.0", "chatgpt-2"
OUT = REPO / "test-results" / "p8-hosted"
STATE = OUT / "state.json"
REPORTS = {  # the saved reports of the benchmark runner (ADR 0008 §35), copied here unchanged
    "DETERMINISTIC": OUT / "reports" / "det_final.json",
    "REPLAY": OUT / "reports" / "replay2.json",
    "LIVE": OUT / "reports" / "live_full.json",
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
    "verification_items",
    "verification_results",
)
# The operator recompute (no model call, no registry write) must leave these tables as they were.
REMEDIATION_UNTOUCHED = (
    "model_runs",
    "skill_nodes",
    "skill_aliases",
    "skill_edges",
    "course_skills",
    "evidence_events",
    "attributions",
    "raw_messages",
)
BASELINE = OUT / "snapshot-pre-remediation.json"
IMMUTABLE = (
    "raw_messages",
    "activity_segments",
    "skill_mappings",
    "attributions",
    "evidence_events",
)

# The defect's shape on the course's loop skill (synthetic text): the AI writes the loop first...
TURN_1 = (
    "How do I loop over a list of names in Python and print each one?",
    "Use a for loop:\n\nfor name in names:\n    print(name)\n\n"
    "The loop takes each item of the list in order and runs the indented line once per item.",
)
# ...then the learner reuses that loop with their own explanation and asks the AI to check it.
TURN_2 = (
    "I used this loop in my homework:\n\nfor name in names:\n    print(name)\n\n"
    "My own explanation: the variable name is set to the first item, the print runs, then name "
    "moves to the next item until the list runs out, so a list of three names prints three "
    "lines. Is my explanation correct?",
    "Yes, your explanation is correct: the loop variable takes each item in turn and the body "
    "runs once per item, so three names give three printed lines.",
)


def save(state: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def load() -> dict:
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def md5_rows(pool, table: str, where: str, *params) -> str:
    return one(
        pool,
        f"select md5(coalesce(json_agg(t order by t::text)::text, '[]')) from public.{table} t "  # noqa: S608
        f"where {where}",
        *params,
    )[0]


def learner_hashes(pool, learner: str) -> dict[str, str]:
    """Per table, a hash of every row the learner owns."""
    return {t: md5_rows(pool, t, "t.learner_id = %s", learner) for t in LEARNER_TABLES}


def learner_counts(pool, learner: str) -> dict[str, int]:
    counts = {
        t: one(pool, f"select count(*) from public.{t} where learner_id = %s", learner)[0]  # noqa: S608
        for t in LEARNER_TABLES
    }
    for table, column in (
        ("feedback", "user_id"),
        ("course_memberships", "user_id"),
        ("profiles", "id"),
    ):
        counts[table] = one(
            pool,
            f"select count(*) from public.{table} where {column} = %s",  # noqa: S608
            learner,
        )[0]
    counts["auth_users"] = one(pool, "select count(*) from auth.users where id = %s", learner)[0]
    return counts


def requests(pool) -> dict[str, int]:
    """Provider requests (cache hits excluded) by model."""
    return dict(
        rows(
            pool,
            "select model, count(*) from public.model_runs where cache_source_run_id is null "
            "group by 1",
        )
    )


def model_run_count(pool) -> int:
    return one(pool, "select count(*) from public.model_runs")[0]


def derive(conn, learner: str, skill: str, policy, now: datetime) -> dict:
    """What the merged code computes for one ledger row (the recompute script's dry run)."""
    records = load_records(conn, uuid.UUID(learner), [uuid.UUID(skill)]).get(uuid.UUID(skill), [])
    default = policy.skill_graph.default_importance
    importance = skill_importance(conn, uuid.UUID(learner), [uuid.UUID(skill)], default).get(
        uuid.UUID(skill), default
    )
    mastery = compute_mastery(
        records, policy.mastery, now, reverification=policy.verification.reverification
    )
    debt = compute_debt(records, mastery, importance=importance, policy=policy.debt, as_of=now)
    return {
        "mastery_state": mastery.state,
        "debt_eligible": debt.eligible,
        "debt_score": round(float(debt.score), 4),
        "delegations": debt.recent_delegation_count,
        "algorithm": ALGORITHM_VERSION,
        "evidence": [
            {
                "id": str(r.id),
                "type": r.evidence_type,
                "actor": r.actor,
                "qualification_reason": r.qualification_reason,
                "delegation": is_delegation(r, policy.debt, now),
            }
            for r in records
        ],
    }


def stored_row(pool, learner: str, skill: str) -> dict | None:
    row = one(
        pool,
        "select mastery_state::text, debt_eligible, round(debt_score::numeric, 4), "
        "recent_delegation_count, algorithm_version, ledger_version, computed_as_of "
        "from public.skill_ledger where learner_id = %s and skill_id = %s",
        learner,
        skill,
    )
    if row is None:
        return None
    keys = ("mastery_state", "debt_eligible", "debt_score", "delegations", "algorithm", "version")
    return dict(zip(keys, (row[0], row[1], float(row[2]), row[3], row[4], row[5]), strict=True)) | {
        "computed_as_of": row[6].isoformat()
    }


def active_recommendations(pool, learner: str) -> dict[str, list]:
    return {
        str(r[0]): [r[1], r[2], r[3]]
        for r in rows(
            pool,
            "select skill_id, type::text, reason_code, id::text from public.recommendations "
            "where learner_id = %s and state = 'ACTIVE'",
            learner,
        )
    }


def consistency(conn, learner: str) -> tuple[list[dict], list[dict]]:
    """Every activity chip (the merged read model) next to its recorded evidence (§24 gate)."""
    evidence = {
        r[0]: r[1:]
        for r in conn.execute(
            "select id, actor::text, evidence_type::text, qualification_reason "
            "from public.evidence_events where learner_id = %s",
            (learner,),
        ).fetchall()
    }
    chips, mismatches = [], []
    for row in list_activity(conn, uuid.UUID(learner), limit=200).items:
        for segment in row.segments:
            for m in segment.mappings:
                if m.evidence_id is None:
                    continue
                recorded = evidence.get(m.evidence_id)
                chip = {
                    "message": str(row.id),
                    "mapping": str(m.mapping_id),
                    "skill": m.canonical_name,
                    "actor": m.actor,
                    "attributed_actor": m.attributed_actor,
                    "evidence_type": m.evidence_type,
                    "qualification_reason": m.qualification_reason,
                }
                chips.append(chip)
                if recorded != (m.actor, m.evidence_type, m.qualification_reason):
                    mismatches.append(chip | {"recorded": recorded})
    return chips, mismatches


def envelope(learner: str, conversation: str, ext: str, parent, index: int, role: str, text: str):
    """The extension's RawActivityEnvelope (CaptureManager + the service worker's course binding)."""
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
        "captured_at": datetime.now(UTC).isoformat(),
        "provider_model": None,
        "revision_index": 0,
        "attachment_metadata": [],
        "context_incomplete": False,
        "active_course_id": COURSE,
        "content_hash": content_hash(text),
        "client_event_id": f"chatgpt:{ext}:r0",
    }


def sign_in(supabase_url: str, anon: str, email: str, password: str) -> str:
    status, body = Http(supabase_url, apikey=anon).call(
        "POST", "/auth/v1/token?grant_type=password", {"email": email, "password": password}
    )
    assert status == 200, f"sign-in failed ({status})"
    return body["access_token"]


def sign_up(supabase_url: str, anon: str, label: str) -> tuple[str, str, str]:
    email = f"skillmirror-p8-{label}-{uuid.uuid4().hex[:12]}@mailinator.com"
    password = f"P8-{secrets.token_urlsafe(18)}"
    status, body = Http(supabase_url, apikey=anon).call(
        "POST", "/auth/v1/signup", {"email": email, "password": password}
    )
    assert status == 200 and body.get("access_token"), f"signup failed ({status})"
    return body["user"]["id"], email, password


def auth_delete(supabase_url: str, user: str) -> int:
    service_key = env_value(REPO / "apps" / "web" / ".env.e2e.local", "SUPABASE_SERVICE_ROLE_KEY")
    request = urllib.request.Request(  # noqa: S310 - the project's own Auth admin endpoint
        f"{supabase_url.rstrip('/')}/auth/v1/admin/users/{user}",
        method="DELETE",
        headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return response.status


def full_snapshot(pool) -> dict:
    with pool.connection() as conn, conn.transaction():
        return snapshot(conn) | {"taken_at": datetime.now(UTC).isoformat()}


# --- phases -----------------------------------------------------------------------------------


def inspect(_args, pool, *_unused) -> Report:
    report = Report()
    migrations = [
        r[0]
        for r in rows(pool, "select version from supabase_migrations.schema_migrations order by 1")
    ]
    report.check(
        "I1",
        "hosted migrations are exactly 0001-0009",
        migrations == EXPECTED_MIGRATIONS,
        migrations,
    )
    open_jobs = rows(
        pool,
        "select job_type, state::text, count(*) from public.processing_jobs "
        "where state not in ('COMPLETED', 'FAILED') group by 1, 2",
    )
    report.check(
        "I2", "no open job anywhere (the smoke worker can only run ours)", not open_jobs, open_jobs
    )
    now = datetime.now(UTC)
    with pool.connection() as conn:
        conn.execute("set transaction read only")
        policy = load_policy(conn)
        derived = derive(conn, AFFECTED_LEARNER, AFFECTED_SKILL, policy, now)
        conn.rollback()
    stored = stored_row(pool, AFFECTED_LEARNER, AFFECTED_SKILL)
    report.check(
        "I3",
        "affected row: stored = the old algorithm's false debt; the merged code derives none",
        stored is not None
        and stored["algorithm"] != ALGORITHM_VERSION
        and stored["debt_eligible"]
        and stored["delegations"] == 2
        and not derived["debt_eligible"]
        and derived["debt_score"] == 0
        and derived["delegations"] == 1,
        {"stored": stored, "merged_code": derived},
    )
    session = one(
        pool,
        "select state::text, failure_code, recommendation_id::text, ready_at, started_at "
        "from public.verification_sessions where id = %s",
        AFFECTED_SESSION,
    )
    report.check("I4", "the READY verification session (reported, never touched)", True, session)
    owner = str(one(pool, "select owner_id from public.courses where id = %s", COURSE)[0])
    state = {
        "inspect": report.results,
        "started_at": now.isoformat(),
        "real_learner_id": owner,
        "real_snapshot": real_snapshot(pool, owner),
        "affected_before": {
            "row": stored,
            "derived": derived,
            "hashes": learner_hashes(pool, AFFECTED_LEARNER),
            "active_recommendations": active_recommendations(pool, AFFECTED_LEARNER),
            "session": md5_rows(pool, "verification_sessions", "t.id = %s", AFFECTED_SESSION),
        },
        "registry": registry(pool),
        "requests": requests(pool),
        "model_runs": model_run_count(pool),
        "benchmark_runs": one(pool, "select count(*) from public.benchmark_runs")[0],
    }
    snap = full_snapshot(pool)
    (OUT / "snapshot-before.json").parent.mkdir(parents=True, exist_ok=True)
    (OUT / "snapshot-before.json").write_text(json.dumps(snap, indent=2), encoding="utf-8")
    save(state)
    return report


def take_baseline(_args, pool, *_unused) -> Report:
    """Read-only: the whole-database snapshot the recompute is judged against (A8). Take it right
    before `recompute_skill.py --apply`, after any other phase that writes."""
    report = Report()
    snap = full_snapshot(pool)
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(json.dumps(snap, indent=2), encoding="utf-8")
    row = stored_row(pool, AFFECTED_LEARNER, AFFECTED_SKILL)
    report.check(
        "B0",
        "pre-remediation snapshot written (read-only); the affected row not yet recomputed",
        row is not None and row["algorithm"] != ALGORITHM_VERSION,
        {"taken_at": snap["taken_at"], "tables": len(snap["tables"]), "row": row},
    )
    return report


def affected(args, pool, *_unused) -> Report:
    report = Report()
    state = load()
    before = state["affected_before"]
    row = stored_row(pool, AFFECTED_LEARNER, AFFECTED_SKILL)
    report.check(
        "A1",
        "ledger recomputed: false delegations 2 -> 1, debt 30.1 -> 0, not eligible",
        row["algorithm"] == ALGORITHM_VERSION
        and row["delegations"] == 1
        and row["debt_score"] == 0
        and not row["debt_eligible"]
        and row["mastery_state"] == before["row"]["mastery_state"],
        {"before": before["row"], "after": row},
    )
    now = datetime.now(UTC)
    with pool.connection() as conn:
        conn.execute("set transaction read only")
        policy = load_policy(conn)
        derived = derive(conn, AFFECTED_LEARNER, AFFECTED_SKILL, policy, now)
        chips, mismatches = consistency(conn, AFFECTED_LEARNER)
        conn.rollback()
    copied = [e for e in derived["evidence"] if e["qualification_reason"] == "COPIED_FROM_AI"]
    report.check(
        "A2",
        "the copy-guard reclassification is not a delegation; a new recompute changes nothing",
        len(copied) == 1
        and not copied[0]["delegation"]
        and {k: derived[k] for k in ("debt_eligible", "debt_score", "delegations", "algorithm")}
        == {k: row[k] for k in ("debt_eligible", "debt_score", "delegations", "algorithm")},
        derived,
    )
    recs = rows(
        pool,
        "select id::text, type::text, reason_code, state::text, resolved_at is not null "
        "from public.recommendations where learner_id = %s and skill_id = %s order by created_at",
        AFFECTED_LEARNER,
        AFFECTED_SKILL,
    )
    active = [r for r in recs if r[3] == "ACTIVE"]
    false_verify = next(r for r in recs if r[0] == AFFECTED_RECOMMENDATION)
    report.check(
        "A3",
        "no false VERIFY: e8d6092f superseded, the skill's action is NO_ACTION, no new VERIFY",
        false_verify[3] == "SUPERSEDED"
        and false_verify[4]
        and len(active) == 1
        and active[0][1] not in ("VERIFY", "REVERIFY"),
        recs,
    )
    after_recs = active_recommendations(pool, AFFECTED_LEARNER)
    others_same = {k: v for k, v in after_recs.items() if k != AFFECTED_SKILL} == {
        k: v for k, v in before["active_recommendations"].items() if k != AFFECTED_SKILL
    }
    report.check(
        "A4", "the learner's other active recommendations unchanged", others_same, len(after_recs)
    )
    defect = [c for c in chips if c["mapping"].startswith(AFFECTED_MAPPING_PREFIX)]
    report.check(
        "A5",
        "activity actor == evidence actor on every chip; the defect chip shows the AI + the reason",
        not mismatches
        and len(defect) == 1
        and defect[0]["actor"] == "AI"
        and defect[0]["attributed_actor"] == "STUDENT"
        and defect[0]["qualification_reason"] == "COPIED_FROM_AI"
        and defect[0]["evidence_type"] == "OBSERVATION",
        {"chips": len(chips), "mismatches": mismatches, "defect_chip": defect},
    )
    hashes = learner_hashes(pool, AFFECTED_LEARNER)
    changed = sorted(t for t in hashes if hashes[t] != before["hashes"][t])
    untouched = {t: hashes[t] == before["hashes"][t] for t in IMMUTABLE}
    report.check(
        "A6",
        "only the derived ledger / recommendations changed; evidence, attributions, raw rows kept",
        set(changed) <= {"skill_ledger", "recommendations"} and all(untouched.values()),
        {"changed": changed, "immutable_unchanged": untouched},
    )
    session = one(
        pool,
        "select s.state::text, s.failure_code, s.recommendation_id::text, r.state::text "
        "from public.verification_sessions s "
        "left join public.recommendations r on r.id = s.recommendation_id where s.id = %s",
        AFFECTED_SESSION,
    )
    report.check(
        "A7",
        "READY verification session untouched (state READY; its recommendation now superseded)",
        md5_rows(pool, "verification_sessions", "t.id = %s", AFFECTED_SESSION) == before["session"]
        and session[0] == "READY",
        session,
    )
    # A8 is relative to the whole-database snapshot taken right before the recompute (`baseline`),
    # not to inspect's absolute counts: the smoke's own rows (model runs, benchmark runs, the
    # admin's audit event) may land in between.
    baseline_file = Path(args.baseline) if args.baseline else BASELINE
    baseline = json.loads(baseline_file.read_text(encoding="utf-8"))
    taken_at = datetime.fromisoformat(baseline["taken_at"]) if "taken_at" in baseline else None
    if taken_at is None:  # a hosted_snapshot.py file: its write time
        taken_at = datetime.fromtimestamp(baseline_file.stat().st_mtime, UTC)
    recomputed_at = one(
        pool,
        "select computed_as_of from public.skill_ledger where learner_id = %s and skill_id = %s",
        AFFECTED_LEARNER,
        AFFECTED_SKILL,
    )[0]
    now_snapshot = full_snapshot(pool)
    changed_tables = sorted(
        t for t, v in now_snapshot["tables"].items() if baseline["tables"].get(t) != v
    )
    created_after = {
        t: one(pool, f"select count(*) from public.{t} where created_at > %s", taken_at)[0]  # noqa: S608
        for t in REMEDIATION_UNTOUCHED
    }
    report.check(
        "A8",
        "the recompute itself: 0 model runs, 0 registry / evidence / attribution / raw rows; "
        "only the ledger and recommendations changed; the real P3B/P4 learner unchanged",
        taken_at < recomputed_at
        and set(changed_tables) <= {"skill_ledger", "recommendations"}
        and all(now_snapshot["tables"][t] == baseline["tables"][t] for t in REMEDIATION_UNTOUCHED)
        and not any(created_after.values())
        and now_snapshot["real_learner_hash"] == baseline["real_learner_hash"]
        and now_snapshot["migrations"] == baseline["migrations"],
        {
            "baseline": baseline_file.name,
            "baseline_taken_at": taken_at.isoformat(),
            "recomputed_at": recomputed_at.isoformat(),
            "changed_tables": changed_tables,
            "rows": {t: now_snapshot["tables"][t]["rows"] for t in REMEDIATION_UNTOUCHED},
            "created_after_baseline": created_after,
            "real_learner": "unchanged"
            if now_snapshot["real_learner_hash"] == baseline["real_learner_hash"]
            else "CHANGED",
        },
    )
    state |= {
        "affected": report.results,
        "affected_after": {
            "row": row,
            "hashes": hashes,
            "active_recommendations": after_recs,
            "session": md5_rows(pool, "verification_sessions", "t.id = %s", AFFECTED_SESSION),
        },
    }
    save(state)
    return report


def readmodel(_args, pool, *_unused) -> Report:
    """Read-only, before the operator recompute: what the merged code already fixes without a
    write (the activity read model, §24) and that nothing of the learner was touched."""
    report = Report()
    state = load()
    before = state["affected_before"]
    with pool.connection() as conn:
        conn.execute("set transaction read only")
        chips, mismatches = consistency(conn, AFFECTED_LEARNER)
        conn.rollback()
    defect = [c for c in chips if c["mapping"].startswith(AFFECTED_MAPPING_PREFIX)]
    report.check(
        "M1",
        "activity actor == evidence actor on every chip; the defect chip shows the AI + the reason",
        not mismatches
        and len(defect) == 1
        and defect[0]["actor"] == "AI"
        and defect[0]["attributed_actor"] == "STUDENT"
        and defect[0]["qualification_reason"] == "COPIED_FROM_AI"
        and defect[0]["evidence_type"] == "OBSERVATION",
        {"chips": len(chips), "mismatches": mismatches, "defect_chip": defect},
    )
    hashes = learner_hashes(pool, AFFECTED_LEARNER)
    report.check(
        "M2",
        "nothing of the affected learner changed (evidence, attributions, ledger, recommendations)",
        hashes == before["hashes"],
        sorted(t for t in hashes if hashes[t] != before["hashes"][t]),
    )
    report.check(
        "M3",
        "READY verification session untouched",
        md5_rows(pool, "verification_sessions", "t.id = %s", AFFECTED_SESSION) == before["session"],
        one(
            pool,
            "select state::text, recommendation_id::text from public.verification_sessions "
            "where id = %s",
            AFFECTED_SESSION,
        ),
    )
    row = stored_row(pool, AFFECTED_LEARNER, AFFECTED_SKILL)
    report.check(
        "M4",
        "the stored row still holds the old algorithm's false debt (operator recompute pending)",
        row == before["row"],
        {"stored": row, "the merged code derives": before["derived"]},
    )
    state["readmodel"] = report.results
    save(state)
    return report


def affected_baseline(state: dict) -> dict[str, str]:
    """The affected learner's row hashes as last proved (after the operator recompute, if run)."""
    return (state.get("affected_after") or state["affected_before"])["hashes"]


def prepare(_args, pool, supabase_url: str, anon: str) -> Report:
    report = Report()
    state = load()
    assert "affected_before" in state, "run inspect first"
    assert not state.get("learner_id"), "a learner is already prepared (run cleanup first)"
    open_jobs = one(
        pool,
        "select count(*) from public.processing_jobs where state not in ('COMPLETED', 'FAILED')",
    )[0]
    assert open_jobs == 0, f"{open_jobs} open jobs on hosted: stop (the worker must only run ours)"
    learner, email, password = sign_up(supabase_url, anon, "learner")
    state |= {"learner_id": learner, "email": email}
    save(state)  # from here on, cleanup can always find the learner
    (OUT / "ui-learner.password").write_text(password, encoding="utf-8")
    with pool.connection() as conn, conn.transaction():
        conn.execute(
            "insert into public.course_memberships (course_id, user_id, role) "
            "values (%s, %s, 'STUDENT') on conflict do nothing",
            (COURSE, learner),
        )
    counts = learner_counts(pool, learner)
    report.check(
        "P1",
        "disposable learner (hosted Auth signup) with a STUDENT membership of 9440004a only",
        counts["profiles"] == 1
        and counts["course_memberships"] == 1
        and counts["raw_messages"] == 0,
        {"learner": learner, "counts": counts},
    )
    state["prepare"] = report.results
    save(state)
    return report


def learner_jobs(pool, learner: str) -> list[tuple]:
    return rows(
        pool,
        "select id::text, entity_id::text, state::text, attempts, outcome from public.processing_jobs "
        "where learner_id = %s and job_type = %s order by created_at",
        learner,
        JOB_PROCESS_RAW_MESSAGE,
    )


def run(args, pool, supabase_url: str, anon: str) -> Report:
    report = Report()
    state = load()
    learner = state["learner_id"]
    password = (OUT / "ui-learner.password").read_text(encoding="utf-8")
    api = Http(args.api, sign_in(supabase_url, anon, state["email"], password))
    settings = get_settings()
    gateway = build_gateway(settings, pool)
    if gateway is None:
        raise SystemExit("GEMINI_API_KEY is not set for the run phase")
    models = {t: gateway.routing.model_for(t) for t in ("TURN_ANALYSIS", "SKILL_ATTRIBUTION")}
    assert set(models.values()) == {OPERATIONAL_MODEL}, f"not the operational runtime: {models}"
    budget = [(s.model, s.used, s.limit, s.reserve) for s in gateway.budget_status()]
    print("routing:", gateway.routing.name, models, "budget:", budget)
    worker = Worker(
        pool,
        {
            JOB_PROCESS_RAW_MESSAGE: partial(
                process_raw_message_job, pool, gateway, mode=settings.turn_analysis_mode
            )
        },
        batch_size=2,
    )  # PROCESS_RAW_MESSAGE only, no daily sweep
    before = requests(pool)
    started = datetime.now(UTC)
    conversation = f"p8-smoke-{uuid.uuid4().hex[:10]}"
    parent, index, turns = None, 0, []
    for number, (question, answer) in enumerate((TURN_1, TURN_2), start=1):
        foreign = one(
            pool,
            "select count(*) from public.processing_jobs where state not in ('COMPLETED', 'FAILED') "
            "and learner_id is distinct from %s",
            learner,
        )[0]
        assert foreign == 0, f"{foreign} open jobs of other learners: stop"
        user_ext, assistant_ext = f"u-{uuid.uuid4().hex[:12]}", f"a-{uuid.uuid4().hex[:12]}"
        batch = {
            "client": {"extension_version": EXTENSION_VERSION, "adapter_version": ADAPTER_VERSION},
            "events": [
                envelope(learner, conversation, user_ext, parent, index, "user", question),
                envelope(
                    learner, conversation, assistant_ext, user_ext, index + 1, "assistant", answer
                ),
            ],
        }
        status, body = api.call(
            "POST", "/v1/events/batch", batch, {"Idempotency-Key": f"p8-smoke-{uuid.uuid4().hex}"}
        )
        assert status == 200 and body["accepted"] == 2, f"ingest {status} {body}"
        user_id, assistant_id = (r["raw_message_id"] for r in body["results"])
        deadline = time.monotonic() + args.timeout
        while True:
            worker.drain(max_rounds=5)
            jobs = learner_jobs(pool, learner)
            if jobs and all(j[2] in ("COMPLETED", "FAILED") for j in jobs):
                break
            if time.monotonic() > deadline:
                raise SystemExit(f"timed out waiting for turn {number}: {jobs}")
            time.sleep(5)
        spent = sum(requests(pool).get(m, 0) - before.get(m, 0) for m in (OPERATIONAL_MODEL,))
        print(f"turn {number}: jobs {[(j[2], j[4]) for j in jobs]}; generation requests {spent}")
        assert spent <= MAX_GENERATION_REQUESTS, f"{spent} generation requests: over the smoke cap"
        turns.append({"user": user_id, "assistant": assistant_id, "conversation": conversation})
        parent, index = assistant_ext, index + 2
    finished = datetime.now(UTC)
    jobs = learner_jobs(pool, learner)
    report.check(
        "R1",
        "both turns captured (chatgpt-2 envelope over HTTP) and processed; every job COMPLETED",
        len(jobs) == 4 and all(j[2] == "COMPLETED" for j in jobs),
        [(j[1][:8], j[2], j[3], j[4]) for j in jobs],
    )
    state |= {
        "turns": turns,
        "jobs": [j[0] for j in jobs],
        "run_window": [started.isoformat(), finished.isoformat()],
        "requests_before_run": before,
        "worker_stats": worker.stats.__dict__,
        "run": report.results,
    }
    save(state)
    return report


def verify(args, pool, supabase_url: str, anon: str) -> Report:
    report = Report()
    state = load()
    learner = state["learner_id"]
    password = (OUT / "ui-learner.password").read_text(encoding="utf-8")
    api = Http(args.api, sign_in(supabase_url, anon, state["email"], password))
    job_traces = [f"job:{j}" for j in state["jobs"]]
    runs = rows(
        pool,
        "select task_type, model, status::text, attempt, cache_source_run_id is not null, trace_id "
        "from public.model_runs where trace_id = any(%s) order by created_at",
        job_traces,
    )
    window = rows(
        pool,
        "select count(*) filter (where trace_id = any(%s)), count(*) from public.model_runs "
        "where created_at between %s and %s",
        job_traces,
        *state["run_window"],
    )[0]
    fresh = [r for r in runs if not r[4]]
    generation = [r for r in fresh if not r[0].startswith("EMBED")]
    report.check(
        "V1",
        "requests of the run: generation on Flash-Lite only, within the cap; none for anyone else",
        generation
        and {r[1] for r in generation} == {OPERATIONAL_MODEL}
        and len(generation) <= MAX_GENERATION_REQUESTS
        and window[0] == window[1],
        {
            "by_task": {
                t: sum(1 for r in fresh if r[0] == t) for t in sorted({r[0] for r in fresh})
            },
            "statuses": sorted({r[2] for r in runs}),
            "cache_hits": sum(1 for r in runs if r[4]),
            "window_ours_of_total": window,
        },
    )
    turns = state["turns"]
    chain = rows(
        pool,
        """select s.anchor_message_id::text, n.canonical_name, m.status::text, a.actor::text,
                  a.proposed_evidence_type, a.student_span, e.id::text, e.actor::text,
                  e.evidence_type::text, e.qualification_reason, e.strength, e.skill_id::text
             from public.activity_segments s
             join public.skill_mappings m on m.segment_id = s.id
             join public.skill_nodes n on n.id = m.skill_id
             left join public.attributions a on a.mapping_id = m.id
             left join public.evidence_events e on e.mapping_id = m.id
            where s.learner_id = %s and m.status = 'ACCEPTED' order by s.created_at""",
        learner,
    )
    first = [c for c in chain if c[0] in (turns[0]["user"], turns[0]["assistant"])]
    second = [c for c in chain if c[0] in (turns[1]["user"], turns[1]["assistant"])]
    report.check(
        "V2",
        "turn 1 (the AI writes the loop): mapped; evidence is the AI's, strength 0 (no mastery)",
        first
        and all(
            c[6] is None or (c[7] == "AI" and c[8] in ("OBSERVATION", "EXPOSURE") and c[10] == 0)
            for c in first
        )
        and any(c[6] is not None for c in first),
        [(c[1], c[3], c[7], c[8], c[9], c[10]) for c in first],
    )
    with pool.connection() as conn:
        policy = load_policy(conn)
    min_chars = policy.attribution.copy_guard_min_chars
    reused = [c for c in second if c[5] and copied_from_ai(c[5], [TURN_1[1]], min_chars)]
    guard_ok = all(c[9] == "COPIED_FROM_AI" and c[7] == "AI" for c in reused if c[6])
    own = [c for c in second if c[7] == "STUDENT" and c[6]]
    report.check(
        "V3",
        "turn 2 (reused loop + own explanation): no reused AI text credited to the learner",
        second and guard_ok,
        {
            "segments": [
                (c[1], c[3], c[4], c[7], c[8], c[9], round(float(c[10] or 0), 4)) for c in second
            ],
            "reused_span_quoted": len(reused),
            "own_explanation_credited (ADR 0008 §26, soft)": [
                (c[8], (c[5] or "")[:80]) for c in own
            ],
        },
    )
    feed = api.get("/v1/activity?limit=50")
    evidence = {
        r[0]: r[1:]
        for r in rows(
            pool,
            "select id::text, actor::text, evidence_type::text, qualification_reason "
            "from public.evidence_events where learner_id = %s",
            learner,
        )
    }
    chips, mismatches = [], []
    for message in feed["items"]:
        for segment in message["segments"]:
            for m in segment["mappings"]:
                if m.get("evidence_id") is None:
                    continue
                chip = {
                    "message_id": message["id"],
                    "mapping_id": m["mapping_id"],
                    "skill_id": m["skill_id"],
                    "skill": m["canonical_name"],
                    "actor": m["actor"],
                    "attributed_actor": m["attributed_actor"],
                    "evidence_type": m["evidence_type"],
                    "qualification_reason": m["qualification_reason"],
                }
                chips.append(chip)
                recorded = evidence.get(m["evidence_id"])
                if recorded != (m["actor"], m["evidence_type"], m["qualification_reason"]):
                    mismatches.append(chip | {"recorded": recorded})
    report.check(
        "V4",
        "GET /v1/activity: every chip's actor, type and reason equal the recorded evidence",
        chips and not mismatches and len(chips) == len(evidence),
        {"chips": len(chips), "evidence": len(evidence), "mismatches": mismatches},
    )
    now = datetime.now(UTC)
    ledger = api.get(f"/v1/ledger?course_id={COURSE}")["skills"]
    touched = sorted({c[11] for c in chain if c[11]})
    derived, stored = {}, {}
    with pool.connection() as conn:
        conn.execute("set transaction read only")
        for skill in touched:
            derived[skill] = derive(conn, learner, skill, policy, now)
        conn.rollback()
    sources = {}
    for skill in touched:
        # Retrieval also searches the global registry (stage 2), so a mapped skill may be outside
        # the course; GET /v1/ledger lists course skills only, the stored row then stands in.
        entry = next((s for s in ledger if s["skill_id"] == skill), None)
        if entry is not None:
            sources[skill] = "GET /v1/ledger"
            stored[skill] = {
                "mastery_state": entry["mastery_state"],
                "debt_eligible": entry["debt_eligible"],
                "debt_score": round(float(entry["debt_score"]), 4),
                "delegations": entry["recent_delegation_count"],
            }
        else:
            sources[skill] = "skill_ledger (global-registry skill)"
            row = stored_row(pool, learner, skill)
            stored[skill] = {k: row[k] for k in ("mastery_state", "debt_eligible", "debt_score")}
            stored[skill]["delegations"] = row["delegations"]
    copied_counted = [
        e
        for d in derived.values()
        for e in d["evidence"]
        if e["qualification_reason"] == "COPIED_FROM_AI" and e["delegation"]
    ]
    report.check(
        "V5",
        "ledger = the merged code's derivation; no copy-guard record counted; no debt from one turn",
        all(
            {k: derived[s][k] for k in stored[s]} == stored[s] and not stored[s]["debt_eligible"]
            for s in touched
        )
        and not copied_counted,
        {
            "stored": stored,
            "sources": sources,
            "delegation_flags": {
                s: [
                    (e["type"], e["actor"], e["qualification_reason"], e["delegation"])
                    for e in d["evidence"]
                ]
                for s, d in derived.items()
            },
        },
    )
    runs_before = model_run_count(pool)
    recs = api.get("/v1/recommendations")["recommendations"]  # every skill, the global one too
    verifications = api.get("/v1/verifications")
    planned = [
        s
        for key in ("preparing", "ready", "in_progress", "pending")
        for s in verifications.get(key, [])
    ]
    report.check(
        "V6",
        "no VERIFY / REVERIFY recommendation and no verification planned; 0 model calls in GETs",
        not [r for r in recs if r["type"] in ("VERIFY", "REVERIFY")]
        and not planned
        and model_run_count(pool) == runs_before,
        {
            "recommendations": sorted(
                {(r["type"], r["reason_code"]) for r in recs if r["skill_id"] in touched}
            ),
            "planned": len(planned),
        },
    )
    details = {s: api.get(f"/v1/skills/{s}") for s in touched}
    timeline = [item for d in details.values() for item in d["evidence"]]
    timeline_ok = all(
        (i["event"]["actor"], i["event"]["evidence_type"], i["event"]["qualification_reason"])
        == evidence[i["event"]["id"]]
        and not (i["event"]["qualification_reason"] == "COPIED_FROM_AI" and i["counts_toward_debt"])
        for i in timeline
    )
    report.check(
        "V7",
        "GET /v1/skills/{id}: timeline = recorded evidence; a copy-guard record never counts as debt",
        timeline and len(timeline) == len(evidence) and timeline_ok,
        [
            (
                i["event"]["actor"],
                i["event"]["evidence_type"],
                i["event"]["qualification_reason"],
                i["counts_toward_mastery"],
                i["counts_toward_debt"],
            )
            for i in timeline
        ],
    )
    counts = learner_counts(pool, learner)
    before_replay = (requests(pool), model_run_count(pool))
    gateway = build_gateway(get_settings(), pool)
    if gateway is None:
        raise SystemExit("GEMINI_API_KEY is not set for the replay (it must make 0 requests)")
    outcomes = []
    for job_id in state["jobs"]:
        entity = one(pool, "select entity_id from public.processing_jobs where id = %s", job_id)[0]
        job = ClaimedJob(
            uuid.UUID(job_id),
            JOB_PROCESS_RAW_MESSAGE,
            "raw_message",
            entity,
            uuid.UUID(learner),
            1,
            3,
        )
        outcomes.append(process_raw_message_job(pool, gateway, job).outcome)
    report.check(
        "V8",
        "replay of every job: 0 provider requests, 0 model runs, 0 new rows",
        (requests(pool), model_run_count(pool)) == before_replay
        and learner_counts(pool, learner) == counts,
        {"outcomes": outcomes, "rows": counts},
    )
    report.check(
        "V9",
        "the real P3B/P4 learner, the affected learner and the registry unchanged",
        real_snapshot(pool, state["real_learner_id"]) == state["real_snapshot"]
        and learner_hashes(pool, AFFECTED_LEARNER) == affected_baseline(state)
        and registry(pool) == state["registry"],
        {"registry": registry(pool)},
    )
    expect = {
        "course_id": COURSE,
        "turn_messages": [t["user"] for t in turns],
        "chips": chips,
        "skills": touched,
    }
    (OUT / "ui-learner.expect.json").write_text(json.dumps(expect, indent=2), encoding="utf-8")
    (OUT / "ui-learner.env").write_text(
        f"P8_LEARNER_EMAIL={state['email']}\nP8_LEARNER_PASSWORD={password}\n", encoding="utf-8"
    )
    state["verify"] = report.results
    save(state)
    return report


def cleanup(_args, pool, supabase_url: str, _anon: str) -> Report:
    report = Report()
    state = load()
    learner = state["learner_id"]
    status = auth_delete(supabase_url, learner)
    remaining = learner_counts(pool, learner)
    report.check(
        "C1",
        "disposable learner deleted through the Auth cascade; nothing learner-owned remains",
        status == 200 and set(remaining.values()) == {0},
        {"admin_delete": status, **remaining},
    )
    orphaned = one(
        pool,
        "select count(*), count(*) filter (where learner_id is null) from public.model_runs "
        "where trace_id = any(%s)",
        [f"job:{j}" for j in state.get("jobs", [])],
    )
    report.check(
        "C2",
        "the run's model runs stay as the quota record, learner id nulled (on delete set null)",
        orphaned[0] == orphaned[1],
        {"model_runs": orphaned[0], "learner_null": orphaned[1]},
    )
    report.check(
        "C3",
        "registry, the real P3B/P4 learner and the affected learner unchanged",
        registry(pool) == state["registry"]
        and real_snapshot(pool, state["real_learner_id"]) == state["real_snapshot"]
        and learner_hashes(pool, AFFECTED_LEARNER) == affected_baseline(state),
        {"registry": registry(pool)},
    )
    for name in ("ui-learner.password", "ui-learner.env"):
        (OUT / name).unlink(missing_ok=True)
    state["cleanup"] = report.results
    state["learner_deleted"] = learner
    state.pop("learner_id")
    save(state)
    return report


BENCHMARK_COLUMNS = (
    "set_name, set_version, mode::text, provider, model, routing, prompt_versions, policy_hash, "
    "code_sha, case_count, passed_count, failed_count, blocked_count, hard_gates, metrics, "
    "verdict::text, provider_requests, embedding_requests, report, started_at, finished_at, id::text"
)


def benchmark(args, pool, supabase_url: str, anon: str) -> Report:
    report = Report()
    state = load()
    recorded = rows(
        pool,
        f"select {BENCHMARK_COLUMNS} from public.benchmark_runs order by created_at",  # noqa: S608
    )
    report.check(
        "B1",
        "hosted benchmark_runs: exactly the three recorded rows",
        len(recorded) == state["benchmark_runs"] + 3 == 3,
        [(r[2], r[10], r[9], r[15]) for r in recorded],
    )
    ids = {}
    for mode, path in REPORTS.items():
        data = json.loads(path.read_text(encoding="utf-8"))
        match = [r for r in recorded if r[2] == mode]
        row = match[0] if len(match) == 1 else None
        results = data["results"]
        expected = {
            "set_name": data["set"],
            "set_version": data["version"],
            "provider": data["provider"],
            "model": data["model"],
            "prompt_versions": data["prompt_versions"],
            "policy_hash": data["policy_hash"],
            "code_sha": data["code_sha"],
            "case_count": len(results),
            "passed_count": sum(1 for r in results if r["passed"] and not r["blocked"]),
            "failed_count": sum(1 for r in results if not r["passed"] and not r["blocked"]),
            "blocked_count": sum(1 for r in results if r["blocked"]),
            "hard_gates": data["hard_gates"],
            "metrics": data["metrics"],
            "verdict": data["verdict"],
            "provider_requests": data["provider_requests"],
            "embedding_requests": data["embedding_requests"],
            "report": {
                "families": data["families"],
                "failing": data["failing"],
                "blocked": data["blocked_ids"],
            },
            "started_at": datetime.fromisoformat(data["started_at"]),
            "finished_at": datetime.fromisoformat(data["finished_at"]),
        }
        actual = (
            dict(
                zip(
                    (
                        "set_name", "set_version", "provider", "model", "prompt_versions",
                        "policy_hash", "code_sha", "case_count", "passed_count", "failed_count",
                        "blocked_count", "hard_gates", "metrics", "verdict", "provider_requests",
                        "embedding_requests", "report", "started_at", "finished_at",
                    ),
                    (*row[0:2], *row[3:5], *row[6:21]),
                    strict=True,
                )
            )
            if row
            else {}
        )  # fmt: skip
        diff = sorted(k for k in expected if actual.get(k) != expected[k])
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        report.check(
            f"B2.{mode[0]}",
            f"{mode}: the row equals the saved report field by field",
            row is not None and not diff,
            {
                "id": row[21] if row else None,
                "result": f"{expected['passed_count']}/{expected['case_count']}",
                "verdict": expected["verdict"],
                "requests": expected["provider_requests"],
                "model": expected["model"],
                "code_sha": expected["code_sha"][:12],
                "report_sha256": sha[:16],
                "diff": diff,
            },
        )
        ids[mode] = row[21] if row else None
    admin = state.get("admin_id")
    if admin is None:
        admin, email, password = sign_up(supabase_url, anon, "admin")
        state |= {"admin_id": admin, "admin_email": email}
        save(state)  # admin-cleanup can always find it
        (OUT / "ui-admin.password").write_text(password, encoding="utf-8")
        with pool.connection() as conn:
            change_role(conn, uuid.UUID(admin), "ADMIN")
    password = (OUT / "ui-admin.password").read_text(encoding="utf-8")
    api = Http(args.api, sign_in(supabase_url, anon, state["admin_email"], password))
    listing = api.get("/v1/admin/benchmark")
    latest = {mode: (run or {}).get("id") for mode, run in listing["latest"].items()}
    report.check(
        "B3",
        "GET /v1/admin/benchmark (disposable ADMIN, audited ROLE_CHANGE): 3 runs, latest per mode",
        len(listing["runs"]) == 3 and latest == ids,
        {
            mode: (run["passed_count"], run["case_count"], run["verdict"], run["provider_requests"])
            for mode, run in listing["latest"].items()
            if run
        },
    )
    (OUT / "ui-admin.env").write_text(
        f"P8_ADMIN_EMAIL={state['admin_email']}\nP8_ADMIN_PASSWORD={password}\n", encoding="utf-8"
    )
    (OUT / "ui-admin.expect.json").write_text(json.dumps({"runs": ids}, indent=2), encoding="utf-8")
    state["benchmark"] = report.results
    state["benchmark_ids"] = ids
    save(state)
    return report


def admin_cleanup(_args, pool, supabase_url: str, _anon: str) -> Report:
    report = Report()
    state = load()
    admin = state["admin_id"]
    status = auth_delete(supabase_url, admin)
    remaining = learner_counts(pool, admin)
    audit = rows(
        pool,
        "select action, actor_id is null from public.audit_events "
        "where entity_id = %s order by created_at",
        admin,
    )
    report.check(
        "D1",
        "disposable admin deleted through the Auth cascade; its ROLE_CHANGE audit row stays",
        status == 200 and set(remaining.values()) == {0} and audit == [("ROLE_CHANGE", True)],
        {"admin_delete": status, "audit": audit, **remaining},
    )
    report.check(
        "D2",
        "registry, the real P3B/P4 learner and the affected learner unchanged",
        registry(pool) == state["registry"]
        and real_snapshot(pool, state["real_learner_id"]) == state["real_snapshot"]
        and learner_hashes(pool, AFFECTED_LEARNER) == affected_baseline(state),
        {"registry": registry(pool)},
    )
    for name in ("ui-admin.password", "ui-admin.env"):
        (OUT / name).unlink(missing_ok=True)
    state["admin_cleanup"] = report.results
    state["admin_deleted"] = admin
    state.pop("admin_id")
    save(state)
    return report


PHASES = {
    "inspect": inspect,
    "readmodel": readmodel,
    "baseline": take_baseline,
    "affected": affected,
    "prepare": prepare,
    "run": run,
    "verify": verify,
    "cleanup": cleanup,
    "benchmark": benchmark,
    "admin-cleanup": admin_cleanup,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("phase", choices=PHASES)
    parser.add_argument("--api", default="http://127.0.0.1:8001")
    parser.add_argument("--timeout", type=int, default=900, help="run: seconds per turn")
    parser.add_argument(
        "--baseline",
        help="affected: the whole-database snapshot taken right before the recompute "
        "(default: test-results/p8-hosted/snapshot-pre-remediation.json from `baseline`)",
    )
    args = parser.parse_args()
    settings = get_settings()
    supabase_url = str(settings.supabase_url)
    anon = env_value(REPO / "apps" / "web" / ".env.local", "NEXT_PUBLIC_SUPABASE_ANON_KEY")
    pool = create_pool(settings.database_url.get_secret_value())
    try:
        result = PHASES[args.phase](args, pool, supabase_url, anon)
    finally:
        pool.close()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"evidence-{args.phase}.json").write_text(
        json.dumps(result.results, indent=2, default=str), encoding="utf-8"
    )
    print(f"{args.phase}: {'PASS' if result.passed else 'FAIL'} ({len(result.results)} checks)")
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
