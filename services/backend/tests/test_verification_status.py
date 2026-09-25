"""The learner-facing status of a verification session (Verification Center; ADR 0007, P9). No DB.

A session the learner never started (PLANNED / READY, no failure) whose skill no longer has an
ACTIVE VERIFY / REVERIFY recommendation is NOT_NEEDED: history, not a current check. Everything
the learner started, and every result, keeps its status whatever the recommendation became.
"""

import pytest

from app.experience.verifications import status_of


@pytest.mark.parametrize(
    ("state", "failure", "outcome", "needed", "status"),
    [
        # A current check: the skill still has its VERIFY / REVERIFY.
        ("PLANNED", None, None, True, "PREPARING"),
        ("READY", None, None, True, "READY"),
        # Its reason is gone (superseded): history, never "Ready".
        ("PLANNED", None, None, False, "NOT_NEEDED"),
        ("READY", None, None, False, "NOT_NEEDED"),
        # Started or finished: unchanged by the recommendation (resumable / history).
        ("IN_PROGRESS", None, None, False, "IN_PROGRESS"),
        ("IN_PROGRESS", None, None, True, "IN_PROGRESS"),
        ("SUBMITTED", None, None, False, "EVALUATING"),
        ("SUBMITTED", "NOT_GRADED", None, False, "NEEDS_REVIEW"),
        ("EVALUATED", None, "CORRECT", False, "PASSED"),
        ("EVALUATED", None, "PARTIAL", True, "PARTIAL"),
        ("EVALUATED", None, "INCORRECT", False, "NOT_PASSED"),
        ("ABANDONED", None, None, False, "ABANDONED"),
        # A failed plan is closed ("Not available"), whatever the recommendation.
        ("PLANNED", "GENERATION_REJECTED", None, False, "NOT_ISSUED"),
        ("PLANNED", "GENERATION_REJECTED", None, True, "NOT_ISSUED"),
    ],
)
def test_status_of(state, failure, outcome, needed, status):
    assert status_of(state, failure, outcome, needed) == status


def test_the_default_keeps_the_p6_statuses():
    assert status_of("READY", None, None) == "READY"
    assert status_of("PLANNED", None, None) == "PREPARING"
