"""Structured output of the course graph bootstrap (architecture §8.2, §8.3).

The model proposes topics and skills using local keys ("t1", "s12"). It never
produces database identifiers: UUIDs are minted only by the canonicalizer.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

AssessmentType = Literal["mcq", "numeric", "code", "sql", "short_response", "reasoning"]

TopicKey = Annotated[str, StringConstraints(pattern=r"^t[0-9]{1,3}$")]
SkillKey = Annotated[str, StringConstraints(pattern=r"^s[0-9]{1,3}$")]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, min_length=8, max_length=400)]
Alias = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class ProposedTopic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: TopicKey
    name: Name
    description: Description


class ProposedSkill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: SkillKey
    canonical_name: Name
    description: Description
    aliases: list[Alias] = Field(max_length=8)
    topic_key: TopicKey
    parent_skill_key: SkillKey | None
    prerequisite_keys: list[SkillKey] = Field(max_length=6)
    related_keys: list[SkillKey] = Field(max_length=6)
    importance: float = Field(ge=0, le=1)
    difficulty_band: int = Field(ge=1, le=5)
    assessment_types: list[AssessmentType] = Field(min_length=1, max_length=4)


class GraphProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topics: list[ProposedTopic] = Field(min_length=1, max_length=20)
    skills: list[ProposedSkill] = Field(min_length=1, max_length=120)
