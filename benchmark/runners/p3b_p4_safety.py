"""P3B/P4 false-debt safety benchmark (architecture §16, §17.3; ADR 0005).

Deterministic: no real model, no database. Each case scripts the SKILL_ATTRIBUTION
model outputs of its captured turns; the runner pushes them through the production
code - the attribution engine's strict validation and single repair, the evidence
qualification, the mastery model and the AI Assistance Debt engine - and scores
the resulting ledger against the labels.

The primary safety metric is the False AI Assistance Debt Rate: the share of
(case, skill) pairs labeled "no actionable debt" that end with actionable debt.
It must be 0: any trivial one-off AI interaction creating actionable debt is a
release blocker (§20.2).

    cd services/backend
    .venv/Scripts/python ../../benchmark/runners/p3b_p4_safety.py [--out report.json]

CI runs the same set (services/backend/tests/test_benchmark_safety.py).
"""

import argparse
import json
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

BENCHMARK = Path(__file__).resolve().parents[1]
BACKEND = BENCHMARK.parent / "services" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.intelligence.attribution.engine import (  # noqa: E402
    AcceptedSkill,
    AttributionRequest,
    attribute_segment,
)
from app.intelligence.debt.engine import compute_debt  # noqa: E402
from app.intelligence.evidence.engine import qualify_attribution  # noqa: E402
from app.intelligence.mastery.engine import EvidenceRecord, compute_mastery  # noqa: E402
from app.intelligence.policy import IntelligencePolicy  # noqa: E402
from app.model_gateway import InMemoryRunRecorder, ModelGateway, RunContext  # noqa: E402
from app.model_gateway.types import ProviderTextResponse  # noqa: E402

# The benchmark clock: every case is evaluated at this instant.
AS_OF = datetime(2026, 9, 25, 12, tzinfo=UTC)
SET_NAME = "p3b-p4-safety"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def load_set(name: str = SET_NAME) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    cases = load_jsonl(BENCHMARK / "cases" / f"{name}.jsonl")
    labels = {
        label["case_id"]: label for label in load_jsonl(BENCHMARK / "labels" / f"{name}.jsonl")
    }
    return [(case, labels[case["case_id"]]) for case in cases]


def architecture_policy() -> IntelligencePolicy:
    """The migration-seeded policy (tests/test_policy_db.py proves the two are identical)."""
    from tests.fakes import policy

    return policy()


def skill_uuid(case_id: str, key: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"skillmirror-benchmark:{case_id}:{key}")


class ScriptedProvider:
    """Answers each attribution request with the case's scripted outputs, in order.

    The last scripted output is repeated (e.g. an invalid output also answers the repair)."""

    name = "benchmark"

    def __init__(self, outputs: list[Any]) -> None:
        self.outputs = outputs
        self.calls = 0

    def generate_json(self, **_: Any) -> ProviderTextResponse:
        out = self.outputs[min(self.calls, len(self.outputs) - 1)]
        self.calls += 1
        return ProviderTextResponse(out if isinstance(out, str) else json.dumps(out))

    def embed(self, **_: Any):  # pragma: no cover - never used
        raise AssertionError("the safety benchmark makes no embedding call")


def _resolve(case_id: str, output: Any) -> Any:
    """Replace benchmark skill keys by their UUIDs in a scripted attribution output."""
    if not isinstance(output, dict) or not isinstance(output.get("attributions"), list):
        return output
    items = []
    for item in output["attributions"]:
        item = dict(item)
        key = item.pop("skill", None)
        if "skill_id" not in item:
            item["skill_id"] = str(skill_uuid(case_id, key))
        items.append(item)
    return {**output, "attributions": items}


@dataclass
class SkillOutcome:
    evidence_events: int
    mastery_state: str
    mastery_gain: bool
    alpha: float
    support: float
    debt_eligible: bool
    debt_actionable: bool
    debt_score: float


@dataclass
class CaseResult:
    case_id: str
    attribution_calls: int = 0
    skills: dict[str, SkillOutcome] = field(default_factory=dict)
    decisions: list[str] = field(default_factory=list)
    error: str | None = None


def run_case(case: dict[str, Any], policy: IntelligencePolicy) -> CaseResult:
    case_id = case["case_id"]
    result = CaseResult(case_id)
    records: dict[str, list[EvidenceRecord]] = {key: [] for key in case["skills"]}
    for turn in case["turns"]:
        if turn["route"] != "MAP":
            continue  # only MAP segments reach mapping, so only they reach attribution
        accepted = [
            m for m in turn["mappings"] if m["confidence"] >= policy.mapping.accept_threshold
        ]
        if not accepted:
            continue
        skills = tuple(
            AcceptedSkill(
                mapping_id=uuid.uuid4(),
                skill_id=skill_uuid(case_id, m["skill"]),
                canonical_name=m["skill"],
                description=f"Benchmark skill {m['skill']}.",
                mapping_confidence=m["confidence"],
                mapping_span=None,
                mapping_reason="DIRECT_ACTION",
                difficulty_band=case["skills"][m["skill"]]["difficulty_band"],
            )
            for m in accepted
        )
        provider = ScriptedProvider([_resolve(case_id, o) for o in turn["attribution_outputs"]])
        gateway = ModelGateway(
            provider,
            InMemoryRunRecorder(),
            generation_model="benchmark-model",
            embedding_model="benchmark-embedding",
            default_timeout=5,
        )
        attributed = attribute_segment(
            gateway,
            AttributionRequest(
                segment_text=turn["user"],
                segment_index=0,
                segment_count=1,
                learner_text=turn["user"],
                assistant_text=turn["assistant"],
                recent_context="\n".join(f"Assistant: {t}" for t in turn["recent_assistant"]),
                course_context=case["course"]["name"],
                skills=skills,
            ),
            RunContext(trace_id=f"benchmark:{case_id}"),
        )
        result.attribution_calls += provider.calls
        if attributed.items is None:
            result.decisions.append(attributed.abstain_reason or "MODEL_OUTPUT_INVALID")
            continue
        occurred_at = AS_OF - timedelta(days=turn["days_ago"])
        for m, skill in zip(accepted, skills, strict=True):
            item = attributed.items[str(skill.skill_id)]
            qualification = qualify_attribution(
                item,
                mapping_confidence=skill.mapping_confidence,
                difficulty_band=skill.difficulty_band,
                learner_text=turn["user"],
                prior_assistant_texts=turn["recent_assistant"],
                attribution_policy=policy.attribution,
                evidence_policy=policy.evidence,
            )
            evidence = qualification.evidence
            # The abstention reason, or the qualification reason of the evidence written.
            result.decisions.append(
                qualification.decision if evidence is None else evidence.qualification_reason
            )
            if evidence is None:
                continue
            records[m["skill"]].append(
                EvidenceRecord(
                    id=uuid.uuid4(),
                    skill_id=skill.skill_id,
                    source_type="AI_ACTIVITY",
                    evidence_type=evidence.evidence_type,
                    actor=evidence.actor,
                    outcome_signal=evidence.outcome_signal,
                    outcome=evidence.outcome,
                    strength=evidence.strength,
                    evidence_confidence=evidence.evidence_confidence,
                    occurred_at=occurred_at,
                    rationale_code=item.reason_code,
                    learning_relevance=turn["learning_relevance"],
                    mapping_status="ACCEPTED",
                )
            )
    for key, spec in case["skills"].items():
        mastery = compute_mastery(records[key], policy.mastery, AS_OF)
        debt = compute_debt(
            records[key], mastery, importance=spec["importance"], policy=policy.debt, as_of=AS_OF
        )
        result.skills[key] = SkillOutcome(
            evidence_events=len(records[key]),
            mastery_state=mastery.state,
            mastery_gain=mastery.alpha > policy.mastery.prior_alpha,
            alpha=round(mastery.alpha, 6),
            support=round(mastery.support, 6),
            debt_eligible=debt.eligible,
            debt_actionable=debt.actionable,
            debt_score=debt.score,
        )
    return result


def score_case(label: dict[str, Any], result: CaseResult) -> list[str]:
    """Return the list of failed checks (empty = pass)."""
    expected = label["expected"]
    failures: list[str] = []
    if result.error:
        return [f"error: {result.error}"]
    calls = expected.get("attribution_calls")
    if calls is not None and result.attribution_calls != calls:
        failures.append(f"attribution_calls {result.attribution_calls} != {calls}")
    for key, want in expected["skills"].items():
        got = result.skills.get(key)
        if got is None:
            failures.append(f"{key}: no outcome")
            continue
        for name in (
            "evidence_events",
            "mastery_state",
            "mastery_gain",
            "debt_eligible",
            "debt_actionable",
        ):
            if name in want and getattr(got, name) != want[name]:
                failures.append(f"{key}.{name} {getattr(got, name)!r} != {want[name]!r}")
        if "max_debt_score" in want and got.debt_score > want["max_debt_score"]:
            failures.append(f"{key}.debt_score {got.debt_score} > {want['max_debt_score']}")
        if "min_debt_score" in want and got.debt_score < want["min_debt_score"]:
            failures.append(f"{key}.debt_score {got.debt_score} < {want['min_debt_score']}")
    return failures


def summarize(pairs: list[tuple[dict[str, Any], CaseResult]]) -> dict[str, Any]:
    negatives = false_debt = positives = true_debt = 0
    for label, result in pairs:
        for key, want in label["expected"]["skills"].items():
            actionable = result.skills[key].debt_actionable if key in result.skills else False
            if want.get("debt_actionable") is False:
                negatives += 1
                false_debt += int(actionable)
            elif want.get("debt_actionable") is True:
                positives += 1
                true_debt += int(actionable)
    return {
        "false_debt_rate": false_debt / negatives if negatives else 0.0,
        "false_debt": false_debt,
        "no_debt_skills": negatives,
        "debt_recall": true_debt / positives if positives else 1.0,
        "debt_skills": positives,
    }


def run(name: str = SET_NAME) -> dict[str, Any]:
    policy = architecture_policy()
    rows, pairs = [], []
    for case, label in load_set(name):
        try:
            result = run_case(case, policy)
        except Exception as exc:  # noqa: BLE001 - reported per case, never a silent pass
            result = CaseResult(case["case_id"], error=f"{type(exc).__name__}: {exc}")
        failures = score_case(label, result)
        pairs.append((label, result))
        rows.append(
            {
                **asdict(result),
                "family": case["family"],
                "passed": not failures,
                "failures": failures,
            }
        )
    summary = summarize(pairs) if all(r["error"] is None for r in rows) else {"error": True}
    return {
        "set": name,
        "cases": len(rows),
        "passed": sum(r["passed"] for r in rows),
        "summary": summary,
        "results": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, help="write the full JSON report here")
    args = parser.parse_args()
    report = run()
    for row in report["results"]:
        status = "PASS" if row["passed"] else "FAIL"
        print(f"{status} {row['case_id']:<45} {'; '.join(row['failures'])}")
    print(f"{report['passed']}/{report['cases']} passed; summary: {json.dumps(report['summary'])}")
    if args.out:
        args.out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return (
        0 if report["passed"] == report["cases"] and report["summary"].get("false_debt") == 0 else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
