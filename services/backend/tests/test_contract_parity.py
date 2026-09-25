"""TS contracts (packages/contracts) == Python contracts == database enums (migrations 0001-0009)."""

import re
from pathlib import Path
from typing import get_args

import pytest

from app.admin import models as admin
from app.api.v1 import me
from app.auth import roles
from app.courses import models as course_models
from app.experience import models as experience
from app.intelligence import contracts
from app.model_gateway import ModelRunStatus
from app.teacher import models as teacher

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
    # P6 (migration 0008)
    "VERIFICATION_STATES": (get_args(contracts.VerificationState), "verification_state"),
    "VERIFICATION_ASSESSMENT_TYPES": (
        get_args(contracts.VerificationAssessmentType),
        "verification_assessment_type",
    ),
    "VERIFICATION_GRADER_TYPES": (
        get_args(contracts.VerificationGraderType),
        "verification_grader_type",
    ),
    "VERIFICATION_EVALUATOR_TYPES": (
        get_args(contracts.VerificationEvaluatorType),
        "verification_evaluator_type",
    ),
    "TRANSFER_DISTANCES": (get_args(contracts.TransferDistance), None),
    "VERIFICATION_STATUSES": (get_args(experience.VerificationStatus), None),
    # P7 (migration 0009)
    "USER_ROLES": (get_args(roles.AppRole), "app_role"),
    "AUDIT_ACTOR_TYPES": (get_args(admin.AuditActorType), "audit_actor_type"),
    "AUDIT_ACTIONS": (get_args(admin.AuditAction), "audit_action"),
    "BENCHMARK_MODES": (get_args(admin.BenchmarkMode), "benchmark_mode"),
    "BENCHMARK_VERDICTS": (get_args(admin.BenchmarkVerdict), "benchmark_verdict"),
    "JOB_RETRY_MODES": (get_args(admin.JobRetryMode), None),
    "CANDIDATE_REVIEW_ACTIONS": (get_args(admin.CandidateReviewAction), None),
    "INDEPENDENT_EVIDENCE_TYPES": (get_args(teacher.IndependentEvidenceType), None),
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
    # P6 verification
    "ChallengeChoice": contracts.ChallengeChoice,
    "RubricCriterion": contracts.RubricCriterion,
    "VerificationGenerationOutput": contracts.VerificationGenerationOutput,
    "CriterionResult": contracts.CriterionResult,
    "VerificationEvaluation": contracts.VerificationEvaluation,
    "VerificationChoice": experience.VerificationChoice,
    "VerificationCriterion": experience.VerificationCriterion,
    "VerificationResult": experience.VerificationResult,
    "VerificationSessionSummary": experience.VerificationSessionSummary,
    "VerificationChallenge": experience.VerificationChallenge,
    "VerificationDetailResponse": experience.VerificationDetailResponse,
    "VerificationBudget": experience.VerificationBudget,
    "VerificationsResponse": experience.VerificationsResponse,
    "VerificationSubmissionRequest": experience.VerificationSubmissionRequest,
    "VerificationSubmissionResponse": experience.VerificationSubmissionResponse,
    # P7 teacher + admin
    "MeCapabilities": me.MeCapabilities,
    "MeResponse": me.MeResponse,
    "TeacherCourse": teacher.TeacherCourse,
    "TeacherCoursesResponse": teacher.TeacherCoursesResponse,
    "TeacherCohort": teacher.TeacherCohort,
    "TeacherSkillRow": teacher.TeacherSkillRow,
    "TeacherMappedSkill": teacher.TeacherMappedSkill,
    "TeacherVerificationNeed": teacher.TeacherVerificationNeed,
    "TeacherEvidenceCounts": teacher.TeacherEvidenceCounts,
    "TeacherCourseOverview": teacher.TeacherCourseOverview,
    "SkillRef": admin.SkillRef,
    "CourseRef": admin.CourseRef,
    "AdminJob": admin.AdminJob,
    "AdminJobsResponse": admin.AdminJobsResponse,
    "JobRetryRequest": admin.JobRetryRequest,
    "JobRetryResponse": admin.JobRetryResponse,
    "AdminModelRun": admin.AdminModelRun,
    "ModelBudgetUsage": admin.ModelBudgetUsage,
    "AdminModelRunsResponse": admin.AdminModelRunsResponse,
    "SimilarSkill": admin.SimilarSkill,
    "AdminSkillCandidate": admin.AdminSkillCandidate,
    "AdminSkillCandidatesResponse": admin.AdminSkillCandidatesResponse,
    "CandidateReviewRequest": admin.CandidateReviewRequest,
    "CandidateReviewResponse": admin.CandidateReviewResponse,
    "BenchmarkRunSummary": admin.BenchmarkRunSummary,
    "AdminBenchmarkResponse": admin.AdminBenchmarkResponse,
    "AuditEventSummary": admin.AuditEventSummary,
    "AdminTotals": admin.AdminTotals,
    "AdminOverview": admin.AdminOverview,
    "AdminCourse": admin.AdminCourse,
    "AdminCoursesResponse": admin.AdminCoursesResponse,
    "AdminMember": admin.AdminMember,
    "AdminCourseDetail": admin.AdminCourseDetail,
    "CourseMemberAddRequest": admin.CourseMemberAddRequest,
    "CourseMemberAddResponse": admin.CourseMemberAddResponse,
    "AdminSkill": admin.AdminSkill,
    "AdminSkillsResponse": admin.AdminSkillsResponse,
    "AdminSkillCourse": admin.AdminSkillCourse,
    "AdminSkillDetail": admin.AdminSkillDetail,
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


def contract_fields(model) -> set[str]:
    """Field names as they appear on the wire (an alias such as A.5 `pass` wins)."""
    return {f.alias or name for name, f in model.model_fields.items()}


@pytest.mark.parametrize("name", sorted(MODEL_PARITY))
def test_typescript_interfaces_match_pydantic_models(name) -> None:
    assert ts_interface_fields(name) == contract_fields(MODEL_PARITY[name])


def test_attribution_evidence_types_are_evidence_types_plus_other() -> None:
    proposed = set(get_args(contracts.AttributionEvidenceType))
    assert proposed - set(get_args(contracts.EvidenceType)) == {"OTHER"}
    # Never proposed by a model: they come from graders, verification and teachers.
    assert not proposed & {"VERIFICATION", "EXECUTION_RESULT", "TEACHER_EVIDENCE"}


@pytest.mark.parametrize(
    "model",
    [
        contracts.AttributionItem,
        contracts.AttributionOutput,
        contracts.VerificationGenerationOutput,
        contracts.ChallengeChoice,
        contracts.RubricCriterion,
        contracts.VerificationEvaluation,
        contracts.CriterionResult,
        experience.VerificationSubmissionRequest,
    ],
)
def test_model_outputs_forbid_extra_fields(model) -> None:
    assert model.model_config.get("extra") == "forbid"


def test_verification_assessment_types_are_the_registry_assessment_types() -> None:
    assert get_args(contracts.VerificationAssessmentType) == get_args(course_models.AssessmentType)


def test_the_learner_challenge_never_carries_the_answer_key_or_rubric() -> None:
    exposed = set(experience.VerificationChallenge.model_fields) | set(
        experience.VerificationSessionSummary.model_fields
    )
    assert not exposed & {"expected_answer", "rubric", "validation", "prompt_fingerprint"}


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


def test_benchmark_detail_extends_the_summary() -> None:
    source = (CONTRACTS / "admin.ts").read_text(encoding="utf-8")
    match = re.search(
        r"export interface BenchmarkRunDetail extends BenchmarkRunSummary \{(.*?)\n\}", source, re.S
    )
    assert match
    extra = set(re.findall(r"^\s+(\w+)\??:", match.group(1), re.M))
    assert extra == set(admin.BenchmarkRunDetail.model_fields) - set(
        admin.BenchmarkRunSummary.model_fields
    )


def test_manual_retry_cap_matches() -> None:
    source = (CONTRACTS / "admin.ts").read_text(encoding="utf-8")
    assert f"export const MAX_MANUAL_RETRIES = {admin.MAX_MANUAL_RETRIES};" in source


def test_teacher_payloads_carry_no_learner_debt_or_actor_field() -> None:
    forbidden = {"learner_id", "user_id", "email", "display_name", "debt_score", "debt_band"}
    forbidden |= {"debt_eligible", "actor", "mastery_mean", "recent_delegation_count"}
    for model in (
        teacher.TeacherCourse,
        teacher.TeacherCohort,
        teacher.TeacherSkillRow,
        teacher.TeacherMappedSkill,
        teacher.TeacherVerificationNeed,
        teacher.TeacherEvidenceCounts,
        teacher.TeacherCourseOverview,
    ):
        assert not set(model.model_fields) & forbidden, model.__name__


def test_admin_payloads_never_carry_model_output_or_captured_text() -> None:
    forbidden = {"output", "content_text", "prompt", "text", "evidence_span", "student_span"}
    for name in MODEL_PARITY:
        model = MODEL_PARITY[name]
        if model.__module__ == admin.__name__:
            assert not set(model.model_fields) & forbidden, name
