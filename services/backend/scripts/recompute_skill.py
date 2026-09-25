"""Recompute one learner's ledger row for one skill with the deployed ledger algorithm, then
reconcile their recommendations (operator action; ADR 0008). No model call; no evidence is
touched (EvidenceEvents are immutable; the ledger and the recommendation queue are derived).

Dry run by default: prints the stored row next to what the current code computes, and each
evidence record with whether it counts as a delegation. `--apply` writes the recompute and the
recommendation refresh, then prints the new row and the active recommendation.

Run it only with the code the hosted runtime runs: a ledger row recomputed by newer code is
recomputed again by the older runtime on the skill's next evidence.

    cd services/backend
    .venv/Scripts/python scripts/recompute_skill.py --learner <uuid> --skill <uuid>
    .venv/Scripts/python scripts/recompute_skill.py --learner <uuid> --skill <uuid> --apply

DATABASE_URL comes from the environment or services/backend/.env.
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.intelligence.debt.engine import compute_debt, is_delegation  # noqa: E402
from app.intelligence.mastery.engine import ALGORITHM_VERSION, compute_mastery  # noqa: E402
from app.intelligence.mastery.ledger import (  # noqa: E402
    load_records,
    recompute_ledger,
    skill_importance,
)
from app.intelligence.policy import load_policy  # noqa: E402
from app.intelligence.recommendations.service import refresh_recommendations  # noqa: E402

LEDGER_SQL = (
    "select mastery_state::text, round(alpha::numeric, 6), debt_eligible, debt_score, "
    "recent_delegation_count, algorithm_version from public.skill_ledger "
    "where learner_id = %s and skill_id = %s"
)
RECOMMENDATION_SQL = (
    "select type::text, reason_code from public.recommendations "
    "where learner_id = %s and skill_id = %s and state = 'ACTIVE'"
)


def dry_run(conn: psycopg.Connection, learner: UUID, skill: UUID) -> None:
    policy = load_policy(conn)
    records = load_records(conn, learner, [skill]).get(skill, [])
    default = policy.skill_graph.default_importance
    importance = skill_importance(conn, learner, [skill], default).get(skill, default)
    now = datetime.now(UTC)
    mastery = compute_mastery(
        records, policy.mastery, now, reverification=policy.verification.reverification
    )
    debt = compute_debt(records, mastery, importance=importance, policy=policy.debt, as_of=now)
    print("stored     :", conn.execute(LEDGER_SQL, (learner, skill)).fetchone())
    print(
        "recomputed :",
        (
            mastery.state,
            round(mastery.alpha, 6),
            debt.eligible,
            debt.score,
            debt.recent_delegation_count,
            ALGORITHM_VERSION,
        ),
    )
    print("active recommendation:", conn.execute(RECOMMENDATION_SQL, (learner, skill)).fetchall())
    for r in records:
        counted = "delegation" if is_delegation(r, policy.debt, now) else "not a delegation"
        print(f"  evidence {r.id} {r.evidence_type} {r.actor} {r.qualification_reason}: {counted}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--learner", type=UUID, required=True)
    parser.add_argument("--skill", type=UUID, required=True)
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = parser.parse_args()
    url = get_settings().database_url
    if url is None:
        raise SystemExit("DATABASE_URL is not set")
    with psycopg.connect(
        url.get_secret_value(), prepare_threshold=None, application_name="skillmirror-recompute"
    ) as conn:
        if not args.apply:
            conn.execute("set transaction read only")
            dry_run(conn, args.learner, args.skill)
            conn.rollback()
            print("dry run: nothing written (use --apply)")
            return 0
        policy = load_policy(conn)
        with conn.transaction():
            recompute_ledger(conn, args.learner, [args.skill], policy=policy)
        refresh_recommendations(conn, args.learner, policy=policy)
        print("recomputed :", conn.execute(LEDGER_SQL, (args.learner, args.skill)).fetchone())
        print(
            "active recommendation:",
            conn.execute(RECOMMENDATION_SQL, (args.learner, args.skill)).fetchall(),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
