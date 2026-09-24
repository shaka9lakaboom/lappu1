"""P2/P3A smoke benchmark: qualification -> routing -> retrieval -> mapping on labeled turns.

Manual, real-model run (not CI): uses the configured Gemini gateway and the
database, and an existing course whose skill graph is READY. Nothing is
persisted except model_runs (trace id `benchmark:<case_id>`).

    cd services/backend
    .venv/Scripts/python ../../benchmark/runners/p3a_smoke.py --course-id <uuid> [--out report.json]

CI only validates the case/label files and this module's scoring
(services/backend/tests/test_benchmark_smoke.py).
"""

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

BENCHMARK = Path(__file__).resolve().parents[1]
BACKEND = BENCHMARK.parent / "services" / "backend"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def load_set(name: str = "p3a-smoke") -> list[tuple[dict[str, Any], dict[str, Any]]]:
    cases = load_jsonl(BENCHMARK / "cases" / f"{name}.jsonl")
    labels = {
        label["case_id"]: label for label in load_jsonl(BENCHMARK / "labels" / f"{name}.jsonl")
    }
    return [(case, labels[case["case_id"]]) for case in cases]


@dataclass
class CaseResult:
    case_id: str
    routes: list[str]
    accepted_skills: list[str] = field(default_factory=list)
    abstained: bool = False
    new_skill_candidate: str | None = None
    error: str | None = None


def score_case(label: dict[str, Any], result: CaseResult) -> list[str]:
    """Return the list of failed checks (empty = pass)."""
    expected = label["expected"]
    failures: list[str] = []
    if result.error:
        return [f"error: {result.error}"]
    allowed = set(expected["routes"])
    bad = [r for r in result.routes if r not in allowed]
    if bad:
        failures.append(f"routes {result.routes} not within {sorted(allowed)}")
    for route in expected.get("must_include_routes", []):
        if route not in result.routes:
            failures.append(f"no segment routed {route}")
    if "min_segments" in expected and len(result.routes) < expected["min_segments"]:
        failures.append(f"{len(result.routes)} segments < {expected['min_segments']}")
    if "max_segments" in expected and len(result.routes) > expected["max_segments"]:
        failures.append(f"{len(result.routes)} segments > {expected['max_segments']}")
    wanted = [w.lower() for w in expected.get("mapped_skill_any", [])]
    if wanted:
        names = [n.lower() for n in result.accepted_skills]
        hit = any(w in n for w in wanted for n in names)
        if not hit and not (result.abstained and expected.get("abstain_allowed")):
            failures.append(
                f"no accepted skill matching {wanted} (accepted: {result.accepted_skills})"
            )
    if "MAP" not in allowed and result.accepted_skills:
        failures.append(f"mapped skills on a non-learning case: {result.accepted_skills}")
    return failures


def run(course_id: str, out: Path | None) -> int:  # pragma: no cover - needs a live model
    sys.path.insert(0, str(BACKEND))
    from app.core.config import get_settings
    from app.db.pool import create_pool
    from app.intelligence.mapping.engine import map_segment
    from app.intelligence.policy import load_policy
    from app.intelligence.relevance.engine import ProcessingUnitText, qualify_unit
    from app.intelligence.relevance.routing import route_segment
    from app.intelligence.retrieval.engine import retrieve_candidates
    from app.model_gateway import RunContext, build_gateway

    settings = get_settings()
    if settings.database_url is None:
        raise SystemExit("DATABASE_URL is not set")
    pool = create_pool(settings.database_url.get_secret_value())
    gateway = build_gateway(settings, pool)
    if gateway is None:
        raise SystemExit("GEMINI_API_KEY is not set")
    with pool.connection() as conn:
        policy = load_policy(conn)

    results, report = [], []
    for case, label in load_set():
        course = case["course"]
        context_text = ", ".join(x for x in (course.get("level"), course.get("subject")) if x)
        course_context = f"{course['name']} ({context_text})" if context_text else course["name"]
        ctx = RunContext(trace_id=f"benchmark:{case['case_id']}")
        unit = ProcessingUnitText(
            user_text=case["turn"]["user"],
            assistant_text=case["turn"]["assistant"],
            recent_context=case["turn"].get("recent_context", ""),
            course_context=course_context,
            context_incomplete=case["turn"]["context_incomplete"],
            attachment_note="1 attachment(s); content not captured"
            if case["turn"]["context_incomplete"]
            else "none",
        )
        result = CaseResult(case_id=case["case_id"], routes=[])
        try:
            qualification = qualify_unit(gateway, unit, policy.qualification, ctx)
            if qualification.segments is None:
                result.routes = ["UNCERTAIN"]
            for segment in qualification.segments or []:
                decision = route_segment(
                    segment, context_incomplete=unit.context_incomplete, policy=policy.qualification
                )
                result.routes.append(decision.route)
                if decision.route != "MAP":
                    continue
                with pool.connection() as conn:
                    retrieval = retrieve_candidates(
                        conn,
                        gateway,
                        query_text=segment.text,
                        course_ids=[course_id],
                        course_context=course_context,
                        policy=policy.retrieval,
                        context=ctx,
                    )
                by_id = {c.skill_id: c for c in retrieval.candidates}
                mapping = map_segment(
                    gateway,
                    segment_text=segment.text,
                    course_context=course_context,
                    candidates=[by_id[i] for i in retrieval.reranked_ids],
                    policy=policy.mapping,
                    context=ctx,
                )
                result.abstained = result.abstained or mapping.outcome == "ABSTAINED"
                result.accepted_skills += [
                    by_id[s.skill_id].canonical_name
                    for s in mapping.skills
                    if s.status == "ACCEPTED"
                ]
                if mapping.new_skill_candidate:
                    result.new_skill_candidate = mapping.new_skill_candidate.canonical_name
        except Exception as exc:  # noqa: BLE001 - report and continue
            result.error = f"{type(exc).__name__}: {exc}"
        failures = score_case(label, result)
        results.append(not failures)
        report.append({**asdict(result), "passed": not failures, "failures": failures})
        print(
            f"{'PASS' if not failures else 'FAIL'} {case['case_id']}: routes={result.routes} "
            f"skills={result.accepted_skills} {'; '.join(failures)}"
        )
    print(f"\n{sum(results)}/{len(results)} passed")
    if out:
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    pool.close()
    return 0 if all(results) else 1


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--course-id", required=True, help="a course whose skill graph is READY")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    raise SystemExit(run(args.course_id, args.out))
