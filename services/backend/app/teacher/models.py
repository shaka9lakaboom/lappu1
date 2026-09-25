"""Teacher API contracts (architecture §12.3, §13; ADR 0008). Mirrored in
packages/contracts/src/teacher.ts. Change both together.

Deliberately basic, cohort-level aggregates of STUDENT members only:

* no per-student rows, learner ids or names;
* no AI-usage counts, per-actor counts, debt scores or debt bands (AI use is not dependency);
* UNKNOWN is its own neutral count ("not enough evidence yet"), never merged into a weakness
  measure and never used to rank;
* below the minimum cohort (policy `teacher_view.min_cohort`, never below 3) nothing but the
  cohort size is shown.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.courses.models import CourseGraphStatus
from app.intelligence.contracts import MasteryState

# The evidence the overview counts: the students' own work and SkillMirror verifications.
# ASSISTED_ATTEMPT / OBSERVATION / EXPOSURE are left out: they describe AI-assisted work, and a
# count of them would be an AI-usage measure.
IndependentEvidenceType = Literal[
    "INDEPENDENT_EXPLANATION",
    "INDEPENDENT_APPLICATION",
    "TRANSFER",
    "EXECUTION_RESULT",
    "TEACHER_EVIDENCE",
]
MIN_COHORT_FLOOR = 3


class TeacherViewPolicy(BaseModel):
    """policy_config `teacher_view` (migration 0009)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    min_cohort: int = Field(ge=MIN_COHORT_FLOOR, le=1000)
    window_days: int = Field(ge=1, le=365)
    top_n: int = Field(ge=1, le=100)


class TeacherCourse(BaseModel):
    id: UUID
    name: str
    subject: str | None
    level: str | None
    graph_status: CourseGraphStatus
    graph_version: int
    skill_count: int = Field(ge=0)
    student_count: int = Field(ge=0)
    # True when the cohort is below the minimum: the overview shows the cohort size only.
    suppressed: bool


class TeacherCoursesResponse(BaseModel):
    min_cohort: int
    courses: list[TeacherCourse]


class TeacherCohort(BaseModel):
    student_count: int = Field(ge=0)
    min_cohort: int
    suppressed: bool


class TeacherSkillRow(BaseModel):
    skill_id: UUID
    name: str
    topic: str | None
    importance: float = Field(ge=0, le=1)
    # Students per mastery state; they add up to the cohort. A student without a ledger row for
    # the skill is UNKNOWN.
    states: dict[MasteryState, int]
    students_with_evidence: int = Field(ge=0)
    verification_need_students: int = Field(ge=0)


class TeacherMappedSkill(BaseModel):
    skill_id: UUID
    name: str
    # Distinct students whose captured learning activity mapped to the skill in the window
    # (accepted mappings the learner did not correct). Not a count of interactions.
    students: int = Field(ge=0)


class TeacherVerificationNeed(BaseModel):
    skill_id: UUID
    name: str
    # Distinct students with an open verification suggestion (VERIFY / REVERIFY) for the skill.
    students: int = Field(ge=0)


class TeacherEvidenceCounts(BaseModel):
    window_days: int
    # Non-excluded evidence of the students' own work (IndependentEvidenceType) and of
    # SkillMirror verifications, on the course's skills.
    independent: int = Field(ge=0)
    verification: int = Field(ge=0)
    students_with_evidence: int = Field(ge=0)
    window_independent: int = Field(ge=0)
    window_verification: int = Field(ge=0)


class TeacherCourseOverview(BaseModel):
    course: TeacherCourse
    cohort: TeacherCohort
    # The oldest ledger computation the state counts read (null when there is none).
    as_of: datetime | None
    # Everything below is null / empty while the cohort is suppressed.
    state_totals: dict[MasteryState, int] | None
    skills: list[TeacherSkillRow]
    common_mapped_skills: list[TeacherMappedSkill]
    verification_needs: list[TeacherVerificationNeed]
    evidence_counts: TeacherEvidenceCounts | None
