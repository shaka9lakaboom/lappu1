"""policy_config: the migration seed is the single source of the intelligence defaults."""

import pytest

from app.intelligence.policy import PolicyConfigError, load_policy
from tests.fakes import ARCHITECTURE_POLICY, policy

pytestmark = pytest.mark.db


def test_seeded_policy_matches_the_architecture_defaults(db_pool) -> None:
    with db_pool.connection() as conn:
        loaded = load_policy(conn)
    assert loaded == policy()
    assert loaded.snapshot() == policy().snapshot()
    assert (
        loaded.retrieval.weights.semantic == ARCHITECTURE_POLICY["retrieval"]["weights"]["semantic"]
    )


def test_missing_policy_fails_loudly(db_pool) -> None:
    with db_pool.connection() as conn, conn.transaction(force_rollback=True):
        conn.execute("delete from public.policy_config where key = 'mapping'")
        with pytest.raises(PolicyConfigError, match="mapping"):
            load_policy(conn)


def test_invalid_policy_fails_loudly(db_pool) -> None:
    with db_pool.connection() as conn, conn.transaction(force_rollback=True):
        conn.execute(
            """update public.policy_config
                  set value = jsonb_set(value, '{accept_threshold}', '0.5')
                where key = 'mapping'"""
        )
        with pytest.raises(PolicyConfigError, match="adjudicate_min"):
            load_policy(conn)
