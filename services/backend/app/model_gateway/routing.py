"""Model routing policy of the ModelGateway (ADR 0004).

The frozen architecture default runs every generation task on one model
(`GEMINI_GENERATION_MODEL`, default `gemini-3.7-flash`). The hackathon free-tier
policy is opt-in: when `GEMINI_ROUTINE_MODEL` is set (e.g. `gemini-3.5-flash-lite`),
high-volume ROUTINE tasks - one per captured turn - run on it, while the
HIGH-VALUE tasks (course graph bootstrap, ambiguous-mapping adjudication) keep
the default model and its separate per-model quota.

Model selection stays configuration: nothing here names a model.
"""

from dataclasses import dataclass

# Task types (engine constants) by tier. tests/test_model_routing.py checks every
# generation task type of the engines is classified.
ROUTINE_TASKS = frozenset(
    {
        "TURN_ANALYSIS",  # combined qualification + rerank + mapping (one per turn)
        "RELEVANCE_CLASSIFICATION",  # staged path
        "SKILL_RERANK",  # staged path
        "SKILL_MAPPING",  # staged path
    }
)
HIGH_VALUE_TASKS = frozenset({"SKILL_GRAPH_BOOTSTRAP", "MAPPING_ADJUDICATION"})


@dataclass(frozen=True)
class ModelRoutingPolicy:
    default_model: str
    routine_model: str | None = None

    @property
    def name(self) -> str:
        return "free-tier" if self.routine_model else "architecture-default"

    def model_for(self, task_type: str) -> str:
        if self.routine_model and task_type in ROUTINE_TASKS:
            return self.routine_model
        return self.default_model

    @property
    def generation_models(self) -> tuple[str, ...]:
        models = [self.default_model]
        if self.routine_model and self.routine_model != self.default_model:
            models.append(self.routine_model)
        return tuple(models)
