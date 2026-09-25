"""The daily stale-ledger sweep (K1; ADR 0008 H7) against PostgreSQL. No model call."""

from datetime import UTC, datetime, timedelta

import pytest

from app.intelligence.mastery.engine import ALGORITHM_VERSION
from app.intelligence.mastery.sweep import sweep_stale_ledgers
from tests.fakes import attribute_all
from tests.test_attribution_consistency_db import (
    CITES_CODE,
    CONFIRMATION,
    OWN_WORK,
    ask_how_to,
    run_turn,
)
from tests.test_evidence_pipeline_db import skill_id
from tests.test_pipeline_db import LOOP_SKILL, bootstrapped_course, fetch

pytestmark = pytest.mark.db


def ledger(pool, learner, skill):
    return fetch(
        pool,
        "select computed_as_of, ledger_version, algorithm_version, debt_eligible, debt_score, "
        "recent_delegation_count from public.skill_ledger where learner_id = %s and skill_id = %s",
        learner,
        skill,
    )[0]


def model_runs(pool) -> int:
    return fetch(pool, "select count(*) from public.model_runs")[0][0]


def seeded(pool, registry, new_learner):
    """A learner with one ledger row (the how-to delegation), computed now."""
    learner = new_learner()
    bootstrapped_course(pool, learner, registry)
    ask_how_to(pool, learner, "sweep")
    return learner, skill_id(pool, registry, LOOP_SKILL)


def age(pool, learner, skill, days: float, **columns) -> None:
    sets = ", ".join(f"{k} = %s" for k in columns)
    with pool.connection() as conn:
        conn.execute(
            f"update public.skill_ledger set computed_as_of = computed_as_of - %s"  # noqa: S608
            f"{', ' + sets if sets else ''} where learner_id = %s and skill_id = %s",
            (timedelta(days=days), *columns.values(), learner, skill),
        )


def test_a_row_computed_before_today_is_derived_again_once(db_pool, registry, new_learner) -> None:
    learner, skill = seeded(db_pool, registry, new_learner)
    age(db_pool, learner, skill, days=3)
    before, runs = ledger(db_pool, learner, skill), model_runs(db_pool)
    now = datetime.now(UTC)
    report = sweep_stale_ledgers(db_pool, now=now)
    assert report.learners >= 1 and report.skills >= 1
    after = ledger(db_pool, learner, skill)
    assert after[0] == now and after[0] > before[0]
    # Nothing but time changed: the values (and ledger_version) are unchanged, only re-derived.
    assert after[1:] == before[1:]
    # Idempotent within the UTC day: nothing of this learner is stale any more.
    sweep_stale_ledgers(db_pool, now=now + timedelta(minutes=5))
    assert ledger(db_pool, learner, skill)[0] == now
    assert model_runs(db_pool) == runs


def test_a_row_of_an_older_algorithm_is_rewritten_the_same_day(
    db_pool, registry, new_learner
) -> None:
    learner, skill = seeded(db_pool, registry, new_learner)
    age(db_pool, learner, skill, days=0, algorithm_version="ledger/p6-v1")
    version = ledger(db_pool, learner, skill)[1]
    sweep_stale_ledgers(db_pool)
    after = ledger(db_pool, learner, skill)
    assert after[2] == ALGORITHM_VERSION and after[1] == version + 1


def test_a_deployment_re_derives_the_hosted_false_debt(
    db_pool, registry, new_learner, monkeypatch
) -> None:
    """The hosted 2026-09-25 row: a pre-validator COPIED_FROM_AI evidence counted as a second
    delegation under ledger/p6-v1 (eligible, 30.1, VERIFY). After the fixed code runs, the sweep
    rewrites it (older algorithm): one delegation, no debt, the VERIFY recommendation gone."""
    from app.intelligence.attribution import engine as attribution_engine

    monkeypatch.setattr(attribution_engine, "copied_from_ai", lambda *args: False)
    learner, skill = seeded(db_pool, registry, new_learner)
    run_turn(
        db_pool,
        learner,
        OWN_WORK,
        CONFIRMATION,
        attribute_all(**CITES_CODE),
        conversation="sweep",
        index=2,
    )
    # The row as ledger/p6-v1 left it on hosted.
    with db_pool.connection() as conn:
        conn.execute(
            "update public.skill_ledger set algorithm_version = 'ledger/p6-v1', debt_eligible = true, "
            "debt_score = 30.143256, recent_delegation_count = 2 "
            "where learner_id = %s and skill_id = %s",
            (learner, skill),
        )
        conn.execute(
            "update public.recommendations set state = 'SUPERSEDED', resolved_at = now() "
            "where learner_id = %s and skill_id = %s and state = 'ACTIVE'",
            (learner, skill),
        )
        conn.execute(
            "insert into public.recommendations (learner_id, skill_id, type, state, priority, "
            "reason_code, mastery_state, inputs, algorithm_version) values (%s, %s, 'VERIFY', "
            "'ACTIVE', 66, 'REPEATED_DELEGATION_UNVERIFIED', 'UNKNOWN', '{}', "
            "'recommendations/p6-v1')",
            (learner, skill),
        )
    sweep_stale_ledgers(db_pool)
    assert ledger(db_pool, learner, skill)[2:] == (ALGORITHM_VERSION, False, 0.0, 1)
    active = fetch(
        db_pool,
        "select type::text from public.recommendations where learner_id = %s and skill_id = %s "
        "and state = 'ACTIVE'",
        learner,
        skill,
    )
    assert active and active[0][0] != "VERIFY"
