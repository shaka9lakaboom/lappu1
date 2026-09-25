"""Applied migrations never change (architecture §18.1): 0001-0008 are on hosted Supabase.

Each file must keep the git blob id it had when it was pushed (0001-0006: main `ab2d73d`;
0007: main `a0e6982`, the P5 merge; 0008: main `4a648ac`, the P6 merge). A schema change is always
a new, higher-numbered migration (P7 = 0009).

The blob id is taken over the content as git stores it (`eol=lf`), so a Windows working copy
with CRLF line endings still compares equal. The next migration to push must be LF on disk: the
Supabase CLI records the file's bytes as the applied statements (hosted 0002 and 0008 carry CRs).
"""

import hashlib
from pathlib import Path

import pytest

MIGRATIONS = Path(__file__).resolve().parents[3] / "supabase" / "migrations"

FROZEN = {
    "0001_p0_foundation.sql": "9bef3edca60c449bb48dc58434d6cd95a618631d",
    "0002_capture_ingestion.sql": "dbedc023a41d6a22eb3b393b572fd883da252e12",
    "0003_courses_skill_graph.sql": "9d03d2f61a2525955041ab80d0831ff42d216f2d",
    "0004_model_gateway_cache.sql": "3eefb84d66b07a7f5da26f5a37ed668f387f85ae",
    "0005_attribution_evidence.sql": "ba13da434adc47336efff13621d5087ee16535ee",
    "0006_mastery_debt.sql": "caac46c582f446bbcfa9327c0bd62f5f64c01156",
    "0007_student_experience.sql": "e62dc91e9590be5d788e7023a074480865461c26",
    "0008_verification.sql": "d44758809dc5148ba77da9df54a556bbbe3ea208",
}
NEXT = "0009_teacher_admin_ops.sql"


def git_blob_id(data: bytes) -> str:
    data = data.replace(b"\r\n", b"\n")
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


@pytest.mark.parametrize("name", sorted(FROZEN))
def test_applied_migration_is_unchanged(name) -> None:
    assert git_blob_id((MIGRATIONS / name).read_bytes()) == FROZEN[name]


def test_migrations_are_numbered_without_gaps() -> None:
    numbers = sorted(int(p.name[:4]) for p in MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    assert numbers == list(range(1, len(numbers) + 1))
    assert (MIGRATIONS / NEXT).exists()


def test_the_next_migration_is_lf_on_disk() -> None:
    assert b"\r" not in (MIGRATIONS / NEXT).read_bytes()
