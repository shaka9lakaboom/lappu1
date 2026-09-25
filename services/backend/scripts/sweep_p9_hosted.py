"""The controlled first P8 stale-ledger sweep on hosted (P9 item 1; manual gate, never in CI).

The first worker of the merged P8 code sweeps at startup (ADR 0008 H7): every ledger row computed
before today's UTC midnight or by an older ledger algorithm is re-derived (`recompute_ledger`, the
pipeline's own code) and the learner's recommendations are refreshed. On hosted that re-derives
the rows of the existing learners (older `ledger/p4-v1` / `ledger/p6-v1` tags) and may insert
deterministic NO_ACTION recommendations. This script runs that maintenance exactly ONCE, under
controlled conditions, so the demo's first startup has nothing left to change:

    snapshot  READ ONLY: whole-database table hashes + per learner with ledger rows: provenance
              hashes (raw messages, segments, decisions, mappings, attributions, evidence,
              corrections, verification sessions/results), the full ledger and recommendation rows
    preview   the sweep inside one transaction that is ROLLED BACK: what it would change
    run       exactly once (refused if it already ran or the database moved since the snapshot):
              sweep_stale_ledgers(pool) - the worker's own function, no model gateway exists here
    verify    READ ONLY: provenance identical, only skill_ledger + recommendations changed, no
              model run created, every ledger row on the current algorithm, per-row changes

    cd services/backend
    .venv/Scripts/python scripts/sweep_p9_hosted.py snapshot
    .venv/Scripts/python scripts/sweep_p9_hosted.py preview
    .venv/Scripts/python scripts/sweep_p9_hosted.py run
    .venv/Scripts/python scripts/sweep_p9_hosted.py verify

DATABASE_URL comes from services/backend/.env and is never printed. Evidence goes to
test-results/p9-hosted/ (git-ignored; ids, counts and hashes only - no captured text).
"""

import argparse
import json
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import psycopg  # noqa: E402
from hosted_snapshot import snapshot as whole_snapshot  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.pool import create_pool  # noqa: E402
from app.intelligence.mastery.engine import ALGORITHM_VERSION  # noqa: E402
from app.intelligence.mastery.sweep import sweep_stale_ledgers, utc_day_start  # noqa: E402

OUT = REPO / "test-results" / "p9-hosted"
BEFORE = OUT / "sweep-before.json"
RAN = OUT / "sweep-run.json"
AFTER = OUT / "sweep-after.json"

# Immutable provenance per learner (column naming the learner).
PROVENANCE = (
    ("raw_messages", "learner_id"),
    ("conversations", "learner_id"),
    ("activity_segments", "learner_id"),
    ("mapping_decisions", "learner_id"),
    ("skill_mappings", "learner_id"),
    ("attributions", "learner_id"),
    ("evidence_events", "learner_id"),
    ("feedback", "user_id"),
    ("verification_sessions", "learner_id"),
    ("verification_results", "learner_id"),
    ("processing_jobs", "learner_id"),
)
# The only tables the sweep may change (derived projections).
DERIVED = ("skill_ledger", "recommendations")
REMEDIATED = ("8afbd2c8", "2761328b")  # the P8 row: debt 0, 1 delegation, not eligible
READY_SESSION = "3aa4481e"


class Report:
    def __init__(self) -> None:
        self.results: list[dict] = []

    def check(self, number: str, name: str, ok: bool, detail: object) -> None:
        self.results.append({"check": number, "name": name, "passed": bool(ok), "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {number:>3} {name}: {detail}")

    @property
    def passed(self) -> bool:
        return all(r["passed"] for r in self.results)


def _rows(conn, sql: str, *params) -> list[dict]:
    return [
        r[0]
        for r in conn.execute(
            f"select to_jsonb(t) from ({sql}) t",  # noqa: S608 - constant queries below
            params,
        ).fetchall()
    ]


def learner_state(conn) -> dict:
    """Per learner with ledger rows: provenance hashes and the full derived rows."""
    learners = [
        str(r[0])
        for r in conn.execute(
            "select distinct learner_id from public.skill_ledger order by 1"
        ).fetchall()
    ]
    state = {}
    for learner in learners:
        provenance = {
            table: conn.execute(
                f"select count(*), md5(coalesce(json_agg(t order by t::text)::text, '[]')) "  # noqa: S608
                f"from public.{table} t where t.{column} = %s",
                (learner,),
            ).fetchone()
            for table, column in PROVENANCE
        }
        state[learner] = {
            "provenance": {t: {"rows": c, "md5": h} for t, (c, h) in provenance.items()},
            "ledger": _rows(
                conn,
                "select * from public.skill_ledger where learner_id = %s order by skill_id",
                learner,
            ),
            "recommendations": _rows(
                conn,
                "select * from public.recommendations where learner_id = %s "
                "order by created_at, id",
                learner,
            ),
        }
    return state


def model_runs(conn) -> dict:
    count, last = conn.execute("select count(*), max(created_at) from public.model_runs").fetchone()
    return {"count": count, "last_created_at": last.isoformat() if last else None}


def read_only_state(pool) -> dict:
    with pool.connection() as conn:
        with conn.transaction():
            conn.execute("set transaction read only")
            state = {
                "taken_at": datetime.now(UTC).isoformat(),
                "whole": whole_snapshot(conn),
                "learners": learner_state(conn),
                "model_runs": model_runs(conn),
                "open_jobs": conn.execute(
                    "select count(*) from public.processing_jobs "
                    "where state not in ('COMPLETED', 'FAILED')"
                ).fetchone()[0],
            }
    return state


def save(path: Path, data: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def short(value: str) -> str:
    return value[:8]


LEDGER_FIELDS = (
    "mastery_state",
    "mastery_mean",
    "support",
    "debt_eligible",
    "debt_score",
    "recent_delegation_count",
    "performance_evidence_count",
    "ledger_version",
    "algorithm_version",
)


def ledger_changes(before: dict, after: dict) -> list[dict]:
    changes = []
    for learner, now in after.items():
        was = {r["skill_id"]: r for r in before.get(learner, {}).get("ledger", [])}
        for row in now["ledger"]:
            old = was.get(row["skill_id"], {})
            diff = {
                f: [old.get(f), row.get(f)]
                for f in LEDGER_FIELDS
                if f in row and old.get(f) != row.get(f)
            }
            changes.append(
                {
                    "learner": short(learner),
                    "skill": short(row["skill_id"]),
                    "changed": diff,
                    "computed_as_of": [old.get("computed_as_of"), row.get("computed_as_of")],
                }
            )
    return changes


def recommendation_changes(before: dict, after: dict) -> dict:
    out = {}
    for learner, now in after.items():
        was = {r["id"]: r for r in before.get(learner, {}).get("recommendations", [])}
        inserted = [r for r in now["recommendations"] if r["id"] not in was]
        superseded = [
            r
            for r in now["recommendations"]
            if r["id"] in was and was[r["id"]]["state"] != r["state"]
        ]
        updated = [
            r
            for r in now["recommendations"]
            if r["id"] in was and was[r["id"]]["state"] == r["state"] and was[r["id"]] != r
        ]
        types: dict[str, int] = {}
        for r in inserted:
            types[r["type"]] = types.get(r["type"], 0) + 1
        out[short(learner)] = {
            "inserted": len(inserted),
            "inserted_by_type": types,
            "state_changed": [
                {"id": short(r["id"]), "type": r["type"], "state": r["state"]} for r in superseded
            ],
            "updated_in_place": len(updated),
            "active_after": sum(1 for r in now["recommendations"] if r["state"] == "ACTIVE"),
        }
    return out


# --- phases -----------------------------------------------------------------------------------


def phase_snapshot(pool) -> Report:
    report = Report()
    state = read_only_state(pool)
    save(BEFORE, state)
    learners = state["learners"]
    report.check(
        "S1",
        "snapshot taken (READ ONLY)",
        True,
        {
            "learners_with_ledger": [short(k) for k in learners],
            "ledger_rows": {
                short(k): [
                    [short(r["skill_id"]), r["algorithm_version"], r["computed_as_of"]]
                    for r in v["ledger"]
                ]
                for k, v in learners.items()
            },
            "recommendations": {short(k): len(v["recommendations"]) for k, v in learners.items()},
            "model_runs": state["model_runs"],
            "open_jobs": state["open_jobs"],
            "migrations": sorted(state["whole"]["migrations"]),
        },
    )
    report.check(
        "S2",
        "no open job (no worker can race the sweep)",
        state["open_jobs"] == 0,
        state["open_jobs"],
    )
    return report


class _OneConnection:
    """A pool whose every connection is the same one, inside a transaction the caller rolls back."""

    def __init__(self, conn) -> None:
        self._conn = conn

    @contextmanager
    def connection(self):
        yield self._conn


def phase_preview(pool) -> Report:
    report = Report()
    before = load(BEFORE)
    with pool.connection() as conn:
        with conn.transaction() as tx:
            # The sweep's own transactions become savepoints of this one, which is rolled back.
            sweep = sweep_stale_ledgers(_OneConnection(conn))
            derived = learner_state(conn)
            raise psycopg.Rollback(tx)
    report.check(
        "P1",
        "preview (rolled back): the sweep's derived changes",
        True,
        {
            "sweep": sweep.__dict__,
            "ledger": ledger_changes(before["learners"], derived),
            "recommendations": recommendation_changes(before["learners"], derived),
        },
    )
    after = read_only_state(pool)
    report.check(
        "P2",
        "nothing persisted by the preview",
        after["whole"]["tables"] == before["whole"]["tables"],
        "whole-database table hashes identical to the snapshot",
    )
    return report


def phase_run(pool) -> Report:
    report = Report()
    if RAN.exists():
        ran = load(RAN)
        raise SystemExit(f"the sweep already ran at {ran['finished_at']}: it runs exactly once")
    before = load(BEFORE)
    now = read_only_state(pool)
    moved = {
        t: [before["whole"]["tables"].get(t), v]
        for t, v in now["whole"]["tables"].items()
        if before["whole"]["tables"].get(t) != v
    }
    if moved or now["open_jobs"]:
        raise SystemExit(
            f"the database moved since the snapshot ({sorted(moved)}; open jobs "
            f"{now['open_jobs']}): take a new snapshot first"
        )
    started = datetime.now(UTC)
    sweep = sweep_stale_ledgers(pool)
    finished = datetime.now(UTC)
    save(
        RAN,
        {
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "sweep": sweep.__dict__,
            "algorithm": ALGORITHM_VERSION,
        },
    )
    report.check(
        "R1",
        "sweep ran once (sweep_stale_ledgers, no model gateway)",
        not sweep.remaining,
        {**sweep.__dict__, "seconds": round((finished - started).total_seconds(), 2)},
    )
    return report


def phase_verify(pool) -> Report:
    report = Report()
    before = load(BEFORE)
    ran = load(RAN)
    after = read_only_state(pool)
    save(AFTER, after)

    changed = sorted(
        t for t, v in after["whole"]["tables"].items() if before["whole"]["tables"].get(t) != v
    )
    report.check(
        "V1",
        "only derived projections changed (skill_ledger, recommendations)",
        set(changed) <= set(DERIVED),
        {"changed_tables": changed},
    )
    provenance_same = {
        short(learner): all(
            before["learners"][learner]["provenance"][t] == v
            for t, v in state["provenance"].items()
        )
        for learner, state in after["learners"].items()
    }
    report.check(
        "V2",
        "evidence / provenance rows unchanged per learner",
        all(provenance_same.values()) and set(after["learners"]) == set(before["learners"]),
        provenance_same,
    )
    report.check(
        "V3",
        "no model call (model_runs unchanged, none created after the snapshot)",
        after["model_runs"] == before["model_runs"],
        {"before": before["model_runs"], "after": after["model_runs"]},
    )
    versions = sorted(
        {r["algorithm_version"] for s in after["learners"].values() for r in s["ledger"]}
    )
    report.check(
        "V4",
        f"every ledger row on {ALGORITHM_VERSION}",
        versions == [ALGORITHM_VERSION],
        versions,
    )
    # The sweep's own staleness rule: computed before the run's UTC day, or an older algorithm.
    day = utc_day_start(datetime.fromisoformat(ran["started_at"]))
    stale = [
        [short(learner), short(r["skill_id"])]
        for learner, s in after["learners"].items()
        for r in s["ledger"]
        if datetime.fromisoformat(r["computed_as_of"]) < day
        or r["algorithm_version"] != ALGORITHM_VERSION
    ]
    report.check(
        "V5", "no stale ledger row remains (the startup sweep has nothing left)", not stale, stale
    )
    remediated = next(
        (
            r
            for learner, s in after["learners"].items()
            if learner.startswith(REMEDIATED[0])
            for r in s["ledger"]
            if r["skill_id"].startswith(REMEDIATED[1])
        ),
        None,
    )
    report.check(
        "V6",
        "the remediated P8 row keeps its values (debt 0, 1 delegation, not eligible)",
        remediated is not None
        and float(remediated["debt_score"]) == 0
        and remediated["debt_eligible"] is False
        and remediated["recent_delegation_count"] == 1,
        {
            k: remediated.get(k)
            for k in ("debt_score", "debt_eligible", "recent_delegation_count", "mastery_state")
        }
        if remediated
        else None,
    )
    report.check(
        "V7",
        "no VERIFY / REVERIFY recommendation created by the sweep",
        not any(
            r["type"] in ("VERIFY", "REVERIFY")
            for learner, s in after["learners"].items()
            for r in s["recommendations"]
            if r["id"] not in {x["id"] for x in before["learners"][learner]["recommendations"]}
        ),
        recommendation_changes(before["learners"], after["learners"]),
    )
    report.check(
        "V8",
        f"READY session {READY_SESSION}… untouched (verification_sessions unchanged)",
        after["whole"]["tables"]["verification_sessions"]
        == before["whole"]["tables"]["verification_sessions"],
        after["whole"]["tables"]["verification_sessions"],
    )
    report.check(
        "V9",
        "migrations unchanged (0001-0009)",
        after["whole"]["migrations"] == before["whole"]["migrations"],
        sorted(after["whole"]["migrations"]),
    )
    report.check(
        "V10",
        "ledger changes (per row)",
        True,
        ledger_changes(before["learners"], after["learners"]),
    )
    return report


PHASES = {
    "snapshot": phase_snapshot,
    "preview": phase_preview,
    "run": phase_run,
    "verify": phase_verify,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("phase", choices=sorted(PHASES))
    args = parser.parse_args()
    settings = get_settings()
    if settings.database_url is None:
        raise SystemExit("DATABASE_URL is not set in services/backend/.env")
    pool = create_pool(settings.database_url.get_secret_value(), max_size=2)
    try:
        report = PHASES[args.phase](pool)
    finally:
        pool.close()
    save(OUT / f"sweep-{args.phase}-report.json", {"results": report.results})
    print(f"{args.phase}: {'PASS' if report.passed else 'FAIL'}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
