"""TS contracts (packages/contracts) == Python contracts == database enums (migrations 0002-0007)."""

import re
from pathlib import Path
from typing import get_args

import pytest

from app.courses import models as course_models
from app.experience import models as experience
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
    # P3B (migration 0005)
    "EVIDENCE_ACTORS": (get_args(contracts.EvidenceActor), "evidence_actor"),
    "EVIDENCE_TYPES": (get_args(contracts.EvidenceType), "evidence_type"),
    "ATTRIBUTION_EVIDENCE_TYPES": (get_args(contracts.AttributionEvidenceType), None),
    "OUTCOME_SIGNALS": (get_args(contracts.OutcomeSignal), "outcome_signal"),
    "EVIDENCE_SOURCE_TYPES": (get_args(contracts.EvidenceSourceType), "evidence_source_type"),
    "ATTRIBUTION_STATUSES": (get_args(contracts.AttributionStatus), "attribution_status"),
    # P4 (migration 0006)
    "MASTERY_STATES": (get_args(contracts.MasteryState), "mastery_state"),
    # P5 (migration 0007)
    "FEEDBACK_ACTIONS": (get_args(contracts.FeedbackAction), "feedback_action"),
    "FEEDBACK_TARGET_TYPES": (get_args(contracts.FeedbackTargetType), "feedback_target_type"),
    "FEEDBACK_VERDICTS": (get_args(contracts.FeedbackVerdict), "feedback_verdict"),
    "RECOMMENDATION_TYPES": (get_args(contracts.RecommendationType), "recommendation_type"),
    "RECOMMENDATION_STATES": (get_args(contracts.RecommendationState), "recommendation_state"),
    "DEBT_BANDS": (get_args(contracts.DebtBand), None),
    "JOB_STATES": (get_args(experience.JobState), "job_state"),
    "FACTOR_LEVELS": (get_args(experience.FactorLevel), None),
    "DEBT_FACTOR_CODES": (get_args(experience.DebtFactorCode), None),
    "MASTERY_GATE_CODES": (get_args(experience.MasteryGateCode), None),
}

# TS interface -> Pydantic model with the same field names.
MODEL_PARITY = {
    "AttributionItem": contracts.AttributionItem,
    "AttributionOutput": contracts.AttributionOutput,
    "EvidenceEvent": contracts.EvidenceEvent,
    "SkillLedgerSummary": contracts.SkillLedgerSummary,
    "LedgerResponse": contracts.LedgerResponse,
    # P5 student experience
    "Recommendation": experience.Recommendation,
    "RecommendationsResponse": experience.RecommendationsResponse,
    "FeedbackRequest": experience.FeedbackRequest,
    "FeedbackSummary": experience.FeedbackSummary,
    "FeedbackResponse": experience.FeedbackResponse,
    "SkillInfo": experience.SkillInfo,
    "SkillCourseContext": experience.SkillCourseContext,
    "SkillPrerequisite": experience.SkillPrerequisite,
    "MasteryGate": experience.MasteryGate,
    "MasteryExplanation": experience.MasteryExplanation,
    "DebtFactor": experience.DebtFactor,
    "DebtExplanation": experience.DebtExplanation,
    "EvidenceSource": experience.EvidenceSource,
    "EvidenceTimelineItem": experience.EvidenceTimelineItem,
    "SkillDetailResponse": experience.SkillDetailResponse,
    "ActivityMappedSkill": experience.ActivityMappedSkill,
    "ActivitySegment": experience.ActivitySegment,
    "ActivityRow": experience.ActivityRow,
    "ActivityResponse": experience.ActivityResponse,
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


def ts_interface_fields(name: str) -> set[str]:
    source = "\n".join(p.read_text(encoding="utf-8") for p in CONTRACTS.glob("*.ts"))
    match = re.search(rf"export interface {name} \{{(.*?)\n\}}", source, re.S)
    assert match, f"interface {name} not found"
    return set(re.findall(r"^\s+(\w+)\??:", match.group(1), re.M))


@pytest.mark.parametrize("name", sorted(MODEL_PARITY))
def test_typescript_interfaces_match_pydantic_models(name) -> None:
    assert ts_interface_fields(name) == set(MODEL_PARITY[name].model_fields)


def test_attribution_evidence_types_are_evidence_types_plus_other() -> None:
    proposed = set(get_args(contracts.AttributionEvidenceType))
    assert proposed - set(get_args(contracts.EvidenceType)) == {"OTHER"}
    # Never proposed by a model: they come from graders, verification and teachers.
    assert not proposed & {"VERIFICATION", "EXECUTION_RESULT", "TEACHER_EVIDENCE"}


@pytest.mark.parametrize("model", [contracts.AttributionItem, contracts.AttributionOutput])
def test_model_outputs_forbid_extra_fields(model) -> None:
    assert model.model_config.get("extra") == "forbid"


def test_feedback_targets_match() -> None:
    source = (CONTRACTS / "experience.ts").read_text(encoding="utf-8")
    block = re.search(r"export const FEEDBACK_TARGETS[^=]*= \{(.*?)\};", source, re.S)
    assert block, "FEEDBACK_TARGETS not found"
    ts = {
        action: tuple(re.findall(r"'([^']*)'", body))
        for action, body in re.findall(r"(\w+): \[(.*?)\]", block.group(1), re.S)
    }
    assert ts == experience.FEEDBACK_TARGETS
    assert set(ts) == set(get_args(contracts.FeedbackAction))


def test_ledger_entry_is_the_ledger_summary() -> None:
    assert contracts.LedgerEntry is contracts.SkillLedgerSummary
    source = (CONTRACTS / "intelligence.ts").read_text(encoding="utf-8")
    assert "export type LedgerEntry = SkillLedgerSummary;" in source


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
