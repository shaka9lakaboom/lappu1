"""TS contracts (packages/contracts) == Python contracts == database enums (migration 0003)."""

import re
from pathlib import Path
from typing import get_args

import pytest

from app.courses import models as course_models
from app.intelligence import contracts
from app.model_gateway import ModelRunStatus

CONTRACTS = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "src"

# TS constant -> (Python values, Postgres enum type or None)
PARITY = {
    "COURSE_STATUSES": (get_args(course_models.CourseStatus), "course_status"),
    "COURSE_GRAPH_STATUSES": (get_args(course_models.CourseGraphStatus), "course_graph_status"),
    "COURSE_MEMBER_ROLES": (get_args(course_models.CourseMemberRole), "course_member_role"),
    "SKILL_NODE_KINDS": (get_args(course_models.SkillNodeKind), "skill_node_kind"),
    "SKILL_STATUSES": (get_args(course_models.SkillStatus), "skill_status"),
    "SKILL_SOURCES": (get_args(course_models.SkillSource), "skill_source"),
    "SKILL_EDGE_TYPES": (get_args(course_models.SkillEdgeType), "skill_edge_type"),
    "ASSESSMENT_TYPES": (get_args(course_models.AssessmentType), None),
    "SEGMENT_CONTEXTS": (get_args(contracts.SegmentContext), "segment_context"),
    "SEGMENT_INTENTS": (get_args(contracts.SegmentIntent), "segment_intent"),
    "LEARNING_RELEVANCE_LEVELS": (get_args(contracts.LearningRelevance), "learning_relevance"),
    "SEGMENT_ROUTES": (get_args(contracts.SegmentRoute), "segment_route"),
    "MAPPING_OUTCOMES": (get_args(contracts.MappingOutcome), "mapping_outcome"),
    "SKILL_MAPPING_STATUSES": (get_args(contracts.SkillMappingStatus), "skill_mapping_status"),
    "SKILL_CANDIDATE_STATUSES": (
        get_args(contracts.SkillCandidateStatus),
        "skill_candidate_status",
    ),
    "MODEL_RUN_STATUSES": (tuple(s.value for s in ModelRunStatus), "model_run_status"),
}


def ts_constants() -> dict[str, tuple[str, ...]]:
    source = "\n".join(p.read_text(encoding="utf-8") for p in CONTRACTS.glob("*.ts"))
    found = {}
    for name, body in re.findall(r"export const (\w+) = \[(.*?)\] as const;", source, re.S):
        found[name] = tuple(re.findall(r"'([^']*)'", body))
    return found


@pytest.mark.parametrize("name", sorted(PARITY))
def test_typescript_matches_python(name) -> None:
    assert ts_constants()[name] == tuple(PARITY[name][0])


@pytest.mark.db
def test_database_enums_match_contracts(db_pool) -> None:
    with db_pool.connection() as conn:
        rows = conn.execute(
            """select t.typname, array_agg(e.enumlabel order by e.enumsortorder)
                 from pg_type t join pg_enum e on e.enumtypid = t.oid
                 join pg_namespace n on n.oid = t.typnamespace
                where n.nspname = 'public' group by t.typname"""
        ).fetchall()
    enums = {name: tuple(labels) for name, labels in rows}
    for name, (values, pg_type) in PARITY.items():
        if pg_type:
            assert enums[pg_type] == tuple(values), name
