"""P5 acceptance: the complete student experience over deterministic fixtures (never run in CI).

P5 makes NO model call. The backend must run WITHOUT GEMINI_API_KEY (so no ModelGateway or
worker exists); this script also checks that `model_runs` does not grow.

Local run (full local Supabase stack: Auth + Postgres):

    npx supabase@2.117.0 start -x realtime,storage-api,imgproxy,mailpit,postgres-meta,studio,edge-runtime,logflare,vector,supavisor
    cd services/backend
    # backend env: DATABASE_URL=<local>, SUPABASE_URL=http://127.0.0.1:54321, no GEMINI_API_KEY
    .venv/Scripts/python -m uvicorn app.main:app --port 8000
    .venv/Scripts/python scripts/acceptance_p5.py --api http://127.0.0.1:8000 \
        --supabase-url http://127.0.0.1:54321 --anon-key <local anon key> \
        --database-url postgresql://postgres:postgres@127.0.0.1:54322/postgres \
        --out ../../test-results/p5-acceptance/evidence.json \
        --ui-credentials ../../test-results/p5-acceptance/ui-learner.env

It signs up two fresh learners through Supabase Auth (real JWTs), seeds both with
tests/p5_fixtures.py (production qualification, persistence, ledger and recommendation
code; no model run), then drives the HTTP API as learner A and checks:

    1 dashboard counts            6 debt explained as a reliance signal
    2 UNKNOWN is neutral          7 DONT_COUNT excludes and recomputes
    3 skill map overlay           8 WRONG_SKILL excludes, keeps provenance
    4 every state explained       9 recommendations update deterministically
    5 evidence traces to activity 10 no model request

Run it on a disposable LOCAL database: its fixture creates prefixed registry rows, so run
`supabase db reset` afterwards (the DB tests expect a clean registry). On hosted, use
acceptance_p5_hosted.py, which reuses an existing course graph and creates no registry row.

Learner B stays seeded for the browser walkthrough (apps/web/e2e/p5-acceptance.spec.ts);
its credentials go to --ui-credentials (a git-ignored file), never to stdout. The evidence
file holds ids and results only - no token or password.
"""

import argparse
import json
import secrets
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.db.pool import create_pool  # noqa: E402
from tests.p5_fixtures import seed_p5_learner  # noqa: E402

PROVENANCE = (
    "raw_messages",
    "activity_segments",
    "mapping_decisions",
    "skill_mappings",
    "attributions",
    "evidence_events",
)
FORBIDDEN_WORDS = ("cheat", "lazy", "bad student", "dishonest", "failing", "punish")


class Http:
    def __init__(self, base: str, token: str | None = None, apikey: str | None = None) -> None:
        self.base, self.token, self.apikey = base.rstrip("/"), token, apikey

    def call(self, method: str, path: str, body: dict | None = None, headers: dict | None = None):
        request = urllib.request.Request(  # noqa: S310 - fixed local/hosted base URL
            f"{self.base}{path}",
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {self.token}"} if self.token else {}),
                **({"apikey": self.apikey} if self.apikey else {}),
                **(headers or {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"null")

    def get(self, path: str):
        status, body = self.call("GET", path)
        assert status == 200, f"GET {path} -> {status} {body}"
        return body


def sign_up(supabase: Http, label: str) -> tuple[str, str, str, str]:
    email = f"skillmirror-p5-{label}-{uuid.uuid4().hex[:10]}@example.test"
    password = f"P5-{secrets.token_urlsafe(18)}"
    status, body = supabase.call("POST", "/auth/v1/signup", {"email": email, "password": password})
    assert status == 200 and body.get("access_token"), (
        f"signup failed ({status}); is email confirmation off?"
    )
    return body["user"]["id"], body["access_token"], email, password


def count(pool, sql: str, *params) -> int:
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchone()[0]


def provenance(pool, learner: str) -> dict[str, int]:
    return {
        t: count(pool, f"select count(*) from public.{t} where learner_id = %s", learner)  # noqa: S608
        for t in PROVENANCE
    }


TOPIC_KINDS = {"DOMAIN", "SUBJECT", "TOPIC"}
ROLE_KEYS = ("for_loops", "while_loops", "comprehensions", "variables", "indexing")


def topic_groups(graph: dict) -> tuple[int, int]:
    """(topic groups, assessable skills) as the Skill Map renders them (lib/courses.ts)."""
    topics = {s["skill"]["id"] for s in graph["skills"] if s["skill"]["node_kind"] in TOPIC_KINDS}
    skills = [
        s["skill"]["id"] for s in graph["skills"] if s["skill"]["node_kind"] not in TOPIC_KINDS
    ]
    topic_of: dict[str, str] = {}
    for edge in graph["edges"]:
        if edge["edge_type"] == "PARENT" and edge["from_skill_id"] in topics:
            topic_of.setdefault(edge["to_skill_id"], edge["from_skill_id"])
    groups = {topic_of[s] for s in skills if s in topic_of}
    return len(groups) + (1 if any(s not in topic_of for s in skills) else 0), len(skills)


def ui_expectations(api: Http, course_id: str, skills: dict[str, str]) -> dict:
    """What the browser walkthrough must see for this learner, read from the API."""
    ledger = api.get(f"/v1/ledger?course_id={course_id}")["skills"]
    counts = dict.fromkeys(("UNKNOWN", "EMERGING", "DEVELOPING", "DEMONSTRATED", "VERIFIED"), 0)
    for entry in ledger:
        counts[entry["mastery_state"]] = counts.get(entry["mastery_state"], 0) + 1
    topics, rows = topic_groups(api.get(f"/v1/courses/{course_id}/skills"))
    debt = api.get(f"/v1/skills/{skills['comprehensions']}")["debt"]
    recs = api.get(f"/v1/recommendations?course_id={course_id}&limit=4")["recommendations"]
    names = {e["skill_id"]: e["canonical_name"] for e in ledger}
    band_word = {"LOW": "Low", "MODERATE": "Moderate", "HIGH": "High"}.get(debt["band"], "No")
    return {
        "counts": counts,
        # After the walkthrough's DONT_COUNT the demonstrated skill is DEVELOPING.
        "counts_after": {
            "DEMONSTRATED": counts["DEMONSTRATED"] - 1,
            "DEVELOPING": counts["DEVELOPING"] + 1,
        },
        "names": {k: names[v] for k, v in skills.items() if k in ROLE_KEYS},
        "topics": topics,
        "skill_rows": rows,
        "debt_band": debt["band"],
        "debt_label": f"{band_word} reliance signal",
        "recommendations": len(recs),
        "recommendations_after": min(4, len(recs) + 1),
        "practice_after": sum(r["type"] == "PRACTICE" for r in recs) + 1,
    }


def write_ui_files(credentials: Path, email: str, password: str, expectations: dict) -> None:
    """Credentials + expectations for the walkthrough, in git-ignored files (never printed)."""
    credentials.parent.mkdir(parents=True, exist_ok=True)
    expect_path = credentials.with_suffix(".expect.json")
    expect_path.write_text(json.dumps(expectations, indent=2), encoding="utf-8")
    credentials.write_text(
        f"P5_ACCEPTANCE_EMAIL={email}\nP5_ACCEPTANCE_PASSWORD={password}\n"
        f"P5_ACCEPTANCE_EXPECT={expect_path.resolve().as_posix()}\n",
        encoding="utf-8",
    )


def check(results: list, number: int, name: str, ok: bool, detail: object) -> None:
    results.append({"check": number, "name": name, "passed": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {number:>2} {name}: {detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description="SkillMirror P5 acceptance (no model call)")
    parser.add_argument("--api", required=True)
    parser.add_argument("--supabase-url", required=True)
    parser.add_argument("--anon-key", required=True)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--ui-credentials", type=Path, default=None)
    args = parser.parse_args()

    pool = create_pool(args.database_url, max_size=4)
    supabase = Http(args.supabase_url, apikey=args.anon_key)
    runs_before = count(pool, "select count(*) from public.model_runs")
    health = Http(args.api).get("/health")

    learner, token, _, _ = sign_up(supabase, "api")
    ui_learner, ui_token, ui_email, ui_password = sign_up(supabase, "ui")
    tag = uuid.uuid4().hex[:6]
    seed = seed_p5_learner(pool, uuid.UUID(learner), f"P5acc{tag} ")
    ui_seed = seed_p5_learner(pool, uuid.UUID(ui_learner), f"P5ui{tag} ")
    api = Http(args.api, token)
    results: list[dict] = []
    skills = {k: str(v) for k, v in seed.skills.items()}
    name_of = {v: k for k, v in skills.items()}

    # 1. Dashboard counts.
    ledger = api.get(f"/v1/ledger?course_id={seed.course_id}")["skills"]
    counts: dict[str, int] = {}
    for entry in ledger:
        counts[entry["mastery_state"]] = counts.get(entry["mastery_state"], 0) + 1
    check(
        results,
        1,
        "dashboard counts",
        counts == {"DEMONSTRATED": 1, "DEVELOPING": 1, "EMERGING": 1, "UNKNOWN": 2},
        counts,
    )

    # 2. UNKNOWN is neutral: no mean, no debt, no ledger row needed.
    unknown = [e for e in ledger if e["mastery_state"] == "UNKNOWN"]
    check(
        results,
        2,
        "UNKNOWN has no mean and no failure signal",
        all(e["mastery_mean"] is None for e in unknown)
        and next(e for e in unknown if e["skill_id"] == skills["while_loops"])["ledger_version"]
        is None,
        {name_of[e["skill_id"]]: (e["mastery_mean"], e["debt_band"]) for e in unknown},
    )

    # 3. Skill map: every assessable graph skill carries a state; no ledger row -> UNKNOWN.
    graph = api.get(f"/v1/courses/{seed.course_id}/skills")
    assessable = [
        s["skill"]["id"]
        for s in graph["skills"]
        if s["skill"]["node_kind"] in ("SKILL", "SUBSKILL")
    ]
    by_id = {e["skill_id"]: e for e in ledger}
    overlay = {name_of[s]: by_id[s]["mastery_state"] for s in assessable}
    check(
        results,
        3,
        "skill map overlays ledger state",
        set(assessable) == set(by_id) and overlay["while_loops"] == "UNKNOWN",
        overlay,
    )

    # 4. Skill detail explains every meaningful state.
    details = {
        k: api.get(f"/v1/skills/{skills[k]}")
        for k in ("for_loops", "while_loops", "comprehensions", "variables", "indexing")
    }
    codes = {
        k: (d["mastery"]["state"], d["mastery"]["explanation_code"], d["recommendation"]["type"])
        for k, d in details.items()
    }
    expected = {
        "for_loops": ("DEMONSTRATED", "INDEPENDENT_EVIDENCE_SUPPORTS", "NO_ACTION"),
        "while_loops": ("UNKNOWN", "NO_EVIDENCE", "NO_ACTION"),
        "comprehensions": ("UNKNOWN", "NO_INDEPENDENT_PERFORMANCE", "VERIFY"),
        "variables": ("EMERGING", "EARLY_DIFFICULTY", "PRACTICE"),
        "indexing": ("DEVELOPING", "MIXED_RESULTS", "PREREQUISITE"),
    }
    leak = any(
        word in json.dumps(d) for d in details.values() for word in ("policy_snapshot", "prompt")
    )
    check(
        results,
        4,
        "every state explained (no prompt/policy leak)",
        codes == expected and not leak,
        codes,
    )

    # 5. Evidence timeline traces to the captured activity.
    traced = 0
    items = [i for d in details.values() for i in d["evidence"]]
    for item in items:
        ids = item["source"]["raw_message_ids"]
        rows = api.get("/v1/activity?" + "&".join(f"raw_message_id={i}" for i in ids))["items"]
        chips = [m["evidence_id"] for r in rows for s in r["segments"] for m in s["mappings"]]
        traced += int({r["id"] for r in rows} == set(ids) and item["event"]["id"] in chips)
    check(
        results,
        5,
        "evidence traces to activity",
        traced == len(items) == 13,
        f"{traced}/{len(items)} events",
    )

    # 6. Debt: a reliance signal with its factors; ineligible skills show none.
    debt = details["comprehensions"]["debt"]
    others = {k: d["debt"]["band"] for k, d in details.items() if k != "comprehensions"}
    text = json.dumps([d["debt"] for d in details.values()]).lower()
    check(
        results,
        6,
        "debt explained without moral judgement",
        debt["band"] == "MODERATE"
        and len(debt["factors"]) == 5
        and set(others.values()) == {"NONE"}
        and not any(w in text for w in FORBIDDEN_WORDS),
        {"band": debt["band"], "factors": [f["code"] for f in debt["factors"]], "others": others},
    )

    # 7. DONT_COUNT excludes one independent application and recomputes the ledger.
    before = provenance(pool, learner)
    target = seed.evidence("for_loops")[0]
    status, body = api.call(
        "POST",
        "/v1/feedback",
        {"action": "DONT_COUNT", "target_type": "EVIDENCE_EVENT", "target_id": str(target)},
        {"Idempotency-Key": f"p5acc-{tag}-1"},
    )
    replay_status, replay = api.call(
        "POST",
        "/v1/feedback",
        {"action": "DONT_COUNT", "target_type": "EVIDENCE_EVENT", "target_id": str(target)},
        {"Idempotency-Key": f"p5acc-{tag}-1"},
    )
    after_for = api.get(f"/v1/skills/{skills['for_loops']}")
    check(
        results,
        7,
        "DONT_COUNT excludes evidence and recomputes the ledger",
        status == 201
        and body["feedback"]["excluded_evidence_ids"] == [str(target)]
        and after_for["mastery"]["state"] == "DEVELOPING"
        and replay_status == 200
        and replay["feedback"]["id"] == body["feedback"]["id"],
        {
            "status": status,
            "state": f"DEMONSTRATED -> {after_for['mastery']['state']}",
            "ledger_version": f"{details['for_loops']['ledger']['ledger_version']} -> {after_for['ledger']['ledger_version']}",
            "replay": replay_status,
        },
    )

    # 8. WRONG_SKILL: exclusion + provenance kept + no invented skill.
    mapping = seed.turns["comprehensions"][0].mapping_id
    status, body = api.call(
        "POST",
        "/v1/feedback",
        {"action": "WRONG_SKILL", "target_type": "SKILL_MAPPING", "target_id": str(mapping)},
        {"Idempotency-Key": f"p5acc-{tag}-2"},
    )
    after = provenance(pool, learner)
    check(
        results,
        8,
        "WRONG_SKILL excludes evidence and preserves provenance",
        status == 201 and len(body["feedback"]["excluded_evidence_ids"]) == 1 and after == before,
        {"status": status, "provenance_before": before, "provenance_after": after},
    )

    # 9. Recommendations update deterministically.
    queue = [
        (r["canonical_name"].split(" ", 1)[1], r["type"])
        for r in api.get("/v1/recommendations")["recommendations"]
    ]
    again = [
        (r["canonical_name"].split(" ", 1)[1], r["type"])
        for r in api.get("/v1/recommendations")["recommendations"]
    ]
    second = seed.turns["comprehensions"][1].evidence_id
    status, _ = api.call(
        "POST",
        "/v1/feedback",
        {"action": "WRONG_SKILL", "target_type": "EVIDENCE_EVENT", "target_id": str(second)},
        {"Idempotency-Key": f"p5acc-{tag}-3"},
    )
    final = {
        r["skill_id"]: r["type"]
        for r in api.get("/v1/recommendations?include_no_action=true")["recommendations"]
    }
    check(
        results,
        9,
        "recommendations update deterministically",
        queue == again
        and dict(queue).get("Writing for loops over sequences") == "PRACTICE"
        and final[skills["comprehensions"]] == "NO_ACTION"
        and status == 201,
        {
            "queue_after_corrections": queue,
            "comprehensions_after_second_wrong_skill": final[skills["comprehensions"]],
        },
    )

    # 10. No model request anywhere.
    runs_after = count(pool, "select count(*) from public.model_runs")
    check(
        results,
        10,
        "no Gemini/model request",
        runs_after == runs_before,
        f"model_runs {runs_before} -> {runs_after}",
    )

    passed = all(r["passed"] for r in results)
    evidence = {
        "passed": passed,
        "backend": {
            "service": health["service"],
            "version": health["version"],
            "environment": health["environment"],
        },
        "learner_id": learner,
        "course_id": str(seed.course_id),
        "skills": skills,
        "ui_learner_id": ui_learner,
        "ui_course_id": str(ui_seed.course_id),
        "results": results,
    }
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    if args.ui_credentials:
        ui_skills = {k: str(v) for k, v in ui_seed.skills.items()}
        expectations = ui_expectations(Http(args.api, ui_token), str(ui_seed.course_id), ui_skills)
        write_ui_files(args.ui_credentials, ui_email, ui_password, expectations)
    pool.close()
    print("P5 ACCEPTANCE:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
