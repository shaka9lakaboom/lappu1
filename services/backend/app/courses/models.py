"""Course and skill-graph API contracts (architecture §7.1, §8, §13).

Mirrored in packages/contracts/src/courses.ts. Change both together.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

CourseStatus = Literal["ACTIVE", "ARCHIVED"]
CourseGraphStatus = Literal["PENDING", "GENERATING", "EMBEDDING", "READY", "FAILED"]
CourseMemberRole = Literal["STUDENT", "TEACHER"]
SkillNodeKind = Literal["DOMAIN", "SUBJECT", "TOPIC", "SKILL", "SUBSKILL"]
SkillStatus = Literal["ACTIVE", "CANDIDATE", "DEPRECATED", "MERGED"]
SkillSource = Literal["COURSE_BOOTSTRAP", "CANDIDATE_APPROVAL", "MANUAL", "SEED"]
SkillEdgeType = Literal["PARENT", "PREREQUISITE", "RELATED"]
AssessmentType = Literal["mcq", "numeric", "code", "sql", "short_response", "reasoning"]


class CourseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    subject: Annotated[str, StringConstraints(min_length=1, max_length=120)] | None = None
    level: Annotated[str, StringConstraints(min_length=1, max_length=60)] | None = None
    description: Annotated[str, StringConstraints(min_length=1, max_length=4000)] | None = None


class Course(BaseModel):
    id: UUID
    name: str
    subject: str | None
    level: str | None
    description: str | None
    status: CourseStatus
    graph_status: CourseGraphStatus
    graph_version: int
    graph_error: str | None
    graph_generated_at: datetime | None
    role: CourseMemberRole
    is_owner: bool
    skill_count: int = Field(ge=0)
    bootstrap_job_state: str | None
    created_at: datetime
    updated_at: datetime


class CourseCreateResponse(BaseModel):
    correlation_id: str
    created: bool
    bootstrap_job_id: UUID | None
    course: Course


class CourseListResponse(BaseModel):
    courses: list[Course]


class CourseMembership(BaseModel):
    course_id: UUID
    user_id: UUID
    role: CourseMemberRole
    created_at: datetime


class SkillNode(BaseModel):
    id: UUID
    slug: str
    canonical_name: str
    description: str
    node_kind: SkillNodeKind
    status: SkillStatus
    version: int
    difficulty_band: int | None
    assessment_types: list[AssessmentType]
    aliases: list[str]


class CourseSkill(BaseModel):
    skill: SkillNode
    importance: float = Field(ge=0, le=1)
    source: SkillSource
    active: bool
    graph_version: int
    embedded: bool


class SkillEdge(BaseModel):
    from_skill_id: UUID
    to_skill_id: UUID
    edge_type: SkillEdgeType
    weight: float


class CourseSkillsResponse(BaseModel):
    course_id: UUID
    graph_status: CourseGraphStatus
    graph_version: int
    skills: list[CourseSkill]
    edges: list[SkillEdge]
