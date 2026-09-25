"""Read-only snapshot of the hosted database around a migration push (manual gate; never in CI).

In ONE `READ ONLY` transaction: every public table's row count and content hash, the real
P3B/P4 learner's row hash, the CR-normalized md5 of each applied migration's statements, the
policy keys, and the connected client sessions by application name (to see whether any worker is
attached). Nothing is written; no secret is printed.

    cd services/backend
    .venv/Scripts/python scripts/hosted_snapshot.py --out ../../test-results/p7-hosted/pre-push.json
    .venv/Scripts/python scripts/hosted_snapshot.py --out ../../test-results/p7-hosted/post-push.json \
        --compare ../../test-results/p7-hosted/pre-push.json
"""

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import psycopg  # noqa: E402

from app.core.config import get_settings  # noqa: E402

COURSE = "9440004a-a25e-4e15-94c0-17c21f6bd695"


def snapshot(conn: psycopg.Connection) -> dict:
    conn.execute("set transaction read only")
    tables = [
        r[0]
        for r in conn.execute(
            "select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = 'public' and c.relkind = 'r' order by 1"
        ).fetchall()
    ]
    data = {}
    for table in tables:
        count, digest = conn.execute(
            f"select count(*), md5(coalesce(string_agg(t::text, E'\\n' order by t::text), '')) "  # noqa: S608
            f"from public.{table} t"
        ).fetchone()
        data[table] = {"rows": count, "md5": digest}
    real = str(
        conn.execute("select owner_id from public.courses where id = %s", (COURSE,)).fetchone()[0]
    )
    real_parts = []
    for table in (
        "raw_messages",
        "activity_segments",
        "mapping_decisions",
        "skill_mappings",
        "attributions",
        "evidence_events",
        "skill_ledger",
        "conversations",
        "processing_jobs",
        "recommendations",
    ):
        real_parts.append(
            conn.execute(
                f"select coalesce(json_agg(t order by t::text), '[]')::text from public.{table} t "  # noqa: S608
                "where t.learner_id = %s",
                (real,),
            ).fetchone()[0]
        )
    real_hash = conn.execute("select md5(%s)", ("\n".join(real_parts),)).fetchone()[0]
    migrations = dict(
        conn.execute(
            "select version, md5(replace(array_to_string(statements, E'\\n'), E'\\r', '')) "
            "from supabase_migrations.schema_migrations order by version"
        ).fetchall()
    )
    policy = [r[0] for r in conn.execute("select key from public.policy_config order by key")]
    sessions = dict(
        conn.execute(
            "select coalesce(nullif(application_name, ''), '(none)'), count(*) "
            "from pg_stat_activity where datname = current_database() and pid <> pg_backend_pid() "
            "group by 1 order by 1"
        ).fetchall()
    )
    return {
        "tables": data,
        "real_learner": real,
        "real_learner_hash": real_hash,
        "migrations": migrations,
        "policy_keys": policy,
        "sessions": sessions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--compare")
    args = parser.parse_args()
    settings = get_settings()
    with psycopg.connect(settings.database_url.get_secret_value(), prepare_threshold=None) as conn:
        with conn.transaction():
            snap = snapshot(conn)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snap, indent=2), encoding="utf-8")
    print(f"tables {len(snap['tables'])}, migrations {sorted(snap['migrations'])}")
    print(
        f"policy keys {len(snap['policy_keys'])}, real learner hash {snap['real_learner_hash'][:12]}"
    )
    print(f"sessions by application: {snap['sessions']}")
    if args.compare:
        before = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        for table, now in snap["tables"].items():
            was = before["tables"].get(table)
            state = "NEW" if was is None else ("same" if was == now else "CHANGED")
            if state != "same":
                print(f"  {table}: {state} {was} -> {now}")
        missing = set(before["tables"]) - set(snap["tables"])
        print(f"  tables missing: {sorted(missing) or 'none'}")
        print(
            "  real learner:",
            "unchanged" if before["real_learner_hash"] == snap["real_learner_hash"] else "CHANGED",
        )
        same = all(snap["migrations"].get(v) == h for v, h in before["migrations"].items())
        print("  earlier migrations:", "unchanged" if same else "CHANGED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
