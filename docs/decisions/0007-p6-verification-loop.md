# 0007 — P6 verification loop: planner, challenges, graders, VERIFICATION evidence

- Status: accepted
- Date: 2026-09-25
- Scope: P6 (architecture §7.1 `verification_*`, §10.3 VERIFIED / NEEDS_REVERIFICATION, §10.4
  VerificationFactor, §11 Verification System, §12.1 Verification Center, §13
  `/v1/verifications`, §16 "Verification", §20.2, Appendix A.4, A.5, B). Teacher / admin (P7,
  migration **0009**) and the benchmark (P8) are out of scope.
- These decisions record how the frozen architecture was implemented where it left a choice. Where
  it is explicit, it was followed. Additive choices are marked as such.

```
P5 VERIFY / REVERIFY recommendation -> deterministic planner -> PLANNED session + GENERATE job
  -> ONE VERIFICATION_GENERATION request per attempt -> deterministic validator -> item + READY
  -> start (IN_PROGRESS; start again = resume) -> submit (Idempotency-Key; the response is stored
     on the session) -> SUBMITTED + GRADE job
  -> deterministic grader (MCQ / numeric / exact short answer), else ONE rubric evaluation
  -> ONE transaction: immutable verification_results + ONE VERIFICATION EvidenceEvent + EVALUATED
  -> recompute_ledger(...) -> refresh_recommendations(...)
```

## Invariants this ADR enforces

| Invariant | Where it is enforced |
| --- | --- |
| Evidence precedes judgement; a result never writes the ledger | `persist.complete_grading` writes result + evidence + state only; the ledger is re-derived by the existing `recompute_ledger`. A DB test proves the ledger is unchanged after grading until the recompute (§20.2 release blocker) |
| Unknown is not weak | UNKNOWN is still the first mastery check, before any verification standing (a long-forgotten verification with support < 1.0 is UNKNOWN) |
| AI use is not dependency; one low-impact AI event never triggers verification | The planner reads only ACTIVE P5 VERIFY / REVERIFY recommendations, never raw AI usage; VERIFY needs actionable debt, which needs ≥ 2 accepted high-confidence delegations |
| Verification never compensates for an uncertain mapping | Debt (hence VERIFY) counts ACCEPTED mappings with evidence confidence ≥ 0.80 only |
| Incomplete verification is not failure; abandoned is not negative evidence | ABANDONED has no result (the 0008 result guard refuses a non-SUBMITTED session) and so no evidence; a network interruption leaves IN_PROGRESS resumable; stale IN_PROGRESS sessions are *abandoned*, never failed |
| An untrusted grade is not a learner failure | Invalid evaluator output (after one repair), `needs_review` or low confidence → no result, no evidence; the session stays SUBMITTED with `failure_code` (NEEDS_REVIEW) |
| AI Assistance Debt ≠ usage count | Verification evidence is never a delegation; a failed verification can raise the factor but never create eligibility |

## Schema (migration 0008)

1. **Numbering.** 0001–0007 are on hosted and unchanged (git blob ids pinned by
   `test_migrations_frozen.py`; pgTAP compares the applied statements' md5 with the hosted ones).
   P6 is **0008**; P7 will be **0009**.
2. **Enums.** `verification_state` (PLANNED, READY, IN_PROGRESS, SUBMITTED, EVALUATED, ABANDONED),
   `verification_assessment_type` (Appendix A.4: mcq, numeric, code, sql, short_response,
   reasoning), `verification_grader_type` (MCQ_EXACT, NUMERIC_TOLERANCE, RUBRIC_AI),
   `verification_evaluator_type` (DETERMINISTIC, AI_RUBRIC).
3. **`verification_sessions`** — the lifecycle and the durable submission. Provenance: learner,
   course (the learner's course with the highest importance for the skill; `set null` on course
   deletion), skill, recommendation (`set null`), `trigger_type` (VERIFY / REVERIFY),
   `reason_code`, the planned difficulty and its band, `plan_day` + `plan_timezone` (the daily
   budget), `planner_version`, `planning_inputs`. Generation bookkeeping: `generation_attempts`
   and `generation_rejections` (reason **codes** per attempt, so a replay rebuilds the exact
   regeneration input). An honest terminal failure: `failure_code` / `failed_at`, only while
   PLANNED (no challenge could be issued) or SUBMITTED (not gradable with confidence).
4. **The submission storage fix.** The earlier prep stored nothing between submit and grading.
   Here the session holds `submitted_response`, `submission_idempotency_key`,
   `submission_request_hash` and `submitted_at` **before** the grading job runs; only the grading
   worker creates `verification_results`. `verification_results` is never a queue.
5. **`verification_items`** — the validated challenge: type, grader type, prompt, `choices`
   (additive to A.4: MCQ options), `expected_answer`, `rubric`, difficulty,
   `prerequisites_used`, `transfer_distance`, `estimated_minutes`, `generator_version`,
   `prompt_version`, `generation_model_run_id`, `generation_attempt`, `prompt_fingerprint`,
   `history_fingerprints`, `validator_version` and the validator report. **Server-only**: RLS on
   and no client privilege at all, so an answer key or rubric can never be read through the Data
   API; the API serves a sanitized challenge. V1: exactly one item per session.
6. **`verification_results`** — the immutable graded result, one per item (unique `item_id`):
   response (exactly the stored submission), score, pass, outcome signal / outcome (see 17),
   evaluation, feedback, grading confidence, evaluator type / version / model run / prompt
   version, policy snapshot. Append-only. **No `evidence_event_id`**: the link is
   `evidence_events.source_type = 'VERIFICATION'`, `source_id = verification_results.id`.
7. **Guards (triggers).**
   - Session insert: starts PLANNED without attempts or failure; an assessable skill; the
     recommendation and course, when named, are the learner's own and concern the skill.
   - Session update: **legal transitions only** — PLANNED→READY (needs its item, not failed),
     READY→IN_PROGRESS, IN_PROGRESS→SUBMITTED, SUBMITTED→EVALUATED (needs its result, not
     failed), IN_PROGRESS→ABANDONED; each may change only its own fields; the plan is immutable;
     EVALUATED / ABANDONED are final; `ON DELETE SET NULL` of course / recommendation is allowed.
   - Item insert: a PLANNED, non-failed session; copies learner / skill; difficulty inside the
     planned band; every prerequisite is a PREREQUISITE edge of the skill; code / sql can never be
     issued (no grader type exists for them — "structurally representable, not issued").
   - Result insert: the item of a SUBMITTED, non-failed session; copies session / learner /
     skill; grades exactly the stored response.
   - **VERIFICATION evidence** (the guard 0005 left to P6): names an existing result of the same
     learner and skill; VERIFICATION type by the STUDENT; outcome signal / outcome / grading
     confidence copied from the result and the difficulty from the item; mapping and attribution
     confidence 1 (so `evidence_confidence = grading_confidence`, B.2).
   - **Ledger:** only the P4-era check `skill_ledger_no_verification_states_before_p6` is dropped
     (0006 itself is unchanged). A new guard requires a non-excluded, passed VERIFICATION
     EvidenceEvent of the skill for VERIFIED / NEEDS_REVERIFICATION.
8. **Uniqueness.** One active session per learner and skill (partial unique index over PLANNED /
   READY / IN_PROGRESS / SUBMITTED without failure); one submission per Idempotency-Key per
   learner; one result per item; one evidence event per result (0005's source key).
9. **Access.** RLS on all three tables. `authenticated` SELECTs its own sessions and results only;
   items are server-only; `anon` nothing; the six new trigger functions are not executable by
   clients. Every write goes through the backend.
10. **Policy key `verification`** (strict `VerificationPolicy`; see *Engineering defaults*). It is
    required by the policy loader, so this branch must not run against hosted before 0008.
11. **Test fixtures.** Since the evidence guard, a VERIFICATION evidence row needs a real result:
    the 0005 and 0007 pgTAP fixtures, and one P5 DB test, now build a graded verification
    first. Their assertions are unchanged. The 0003 pgTAP check "no verification tables yet"
    now asserts they arrive with 0008. The 0006 "VERIFIED refused" checks still pass (the new
    guard refuses VERIFIED without verification evidence).

## Planner (Engine 13, deterministic, no model call)

12. **Input: the P5 recommendation, never raw AI usage.** ACTIVE VERIFY / REVERIFY rows only.
    A skill is skipped while it has an active session, or during a cooldown: after a pass (7 d,
    §11.1), after a failure (1 d), after an abandonment (1 d), or after a pipeline failure (24 h).
    Candidates are ordered by recommendation priority, course importance, skill id.
13. **Daily learner burden (Appendix B: 2).** At most `max_daily_unsolicited` sessions are planned
    per **learner-day**, counting every session planned that day in any state (so a failed
    generation also consumes budget, bounding model calls at 2 × 3 attempts per learner-day).
    The day boundary is the learner's `profiles.timezone` when it is a valid IANA zone, **else
    UTC** (decision: the profile field exists since P0 and defaults to UTC; an invalid value
    never blocks planning).
14. **When it runs.** `GET /v1/verifications` refreshes the recommendation queue (P5
    `refresh_recommendations`), plans (deterministic cache state only) and returns the queue. No
    cron, no extra service, no model call in the request. The same run abandons IN_PROGRESS
    sessions untouched for 14 days and enqueues any missing generation / grading job.
15. **Idempotency.** Under the learner's `verification:{learner}` advisory lock, one transaction
    writes the PLANNED session and its GENERATE_VERIFICATION job (`processing_jobs` unique per
    type and entity); a repeated or concurrent run creates nothing new.
16. **Difficulty.** The skill's 1–5 band normalized as evidence does (`difficulty_for`), clamped to
    [0.1, 0.9], with a ±0.15 band. Appendix B.1's multiplier is only valid inside it; the
    validator and the item guard enforce it.

## Generation (Engine 14) and validation

17. **One `VERIFICATION_GENERATION` request per attempt** (`verification-generation/v1`), through
    the ModelGateway only (no model name or SDK in `app/intelligence/verification`). Output:
    Appendix A.4 plus `choices` for MCQ; extra fields forbidden. The prompt asks for a fresh
    context, transfer where feasible, the planned band, known prerequisites only, and "changing
    only the numbers is not enough". Allowed types: the policy's supported types, the skill's own
    types first and the deterministically graded ones (mcq, numeric) first within each group.
18. **Cache semantics.** The input carries the session id, the attempt number, the earlier
    attempts' rejection reasons and the fingerprints / previews of the learner's earlier
    challenges for the skill (bounded: 5). So separate sessions never share a challenge, a
    regeneration never gets the rejected cached output back, and a replay of the same attempt is
    served by the exact result cache (0 provider requests). Captured learner text is never sent to
    the generator.
19. **Deterministic validator (§11.3).** Target skill exact; difficulty in the band; type supported
    (code / sql → `SANDBOX_UNAVAILABLE`); prerequisites known; prompt well formed; estimated time
    1–15 min; MCQ: 2–6 unique keys A–F with distinct texts and an answer key naming existing
    options but not all; numeric: the key parses as one number; rubric types: 1+ distinct
    criteria; not a material duplicate of an earlier challenge or of the source activity (word
    Jaccard ≥ 0.8) and not an earlier prompt with only its numbers changed. **Initial + 2
    regenerations**; all rejected → no challenge, the session records `GENERATION_REJECTED`.
    Schema-invalid output gets the gateway's single repair first; still invalid counts as a
    rejected attempt (`MODEL_OUTPUT_INVALID`).
20. **Hackathon P6 types:** mcq, numeric, short_response, reasoning. Code / SQL stay representable
    (enum, contract) but are never issued: the policy validator refuses them as supported types,
    the validator rejects them, and no grader type exists. The deterministic sandbox is post-V1;
    an LLM rubric is not treated as its equivalent.

## Grading (Engine 15)

21. **Deterministic first, no model call:** MCQ exact set; numeric parse + tolerance
    `max(1e-6, 0.5% × |expected|)`; a short response equal (normalized) to the answer key passes.
    Anything else goes to the rubric evaluator — a different wording is never a deterministic
    failure. Grading confidence 1.0.
22. **Rubric evaluator** (`VERIFICATION_EVALUATION`, `verification-evaluation/v1`, Appendix A.5,
    extra fields forbidden). The learner answer is framed `<<< >>>` as untrusted data. The output
    must give exactly one result per rubric criterion (copied, in order), a score equal to the met
    share of the rubric points (±0.05) and `pass` = score ≥ 0.7; invalid → one repair → still
    invalid, `needs_review`, or confidence < 0.7 → **no result, no evidence**
    (`EVALUATION_INVALID_OUTPUT` / `_NEEDS_REVIEW` / `_LOW_CONFIDENCE`). The score used is the
    deterministic met-points share. Grading confidence = the evaluator's confidence.
23. **Outcome mapping (deterministic from the grade):** pass → CORRECT (1.0); score 0 →
    INCORRECT (0.0); otherwise PARTIAL (outcome = score). Enforced by checks on the result.
24. **Backpressure.** A 429/503 or a spent budget on generation or evaluation propagates; the
    worker defers the job with **0 attempts spent** (ADR 0004); the submission is untouched.

## Result → evidence and the ledger

25. **One EvidenceEvent per result:** `source_type = VERIFICATION`, `source_id = result.id`,
    STUDENT, VERIFICATION, independence and base weight from the evidence policy (1.0, 1.5),
    difficulty multiplier from the item's difficulty, `mapping = attribution = 1`, evidence
    confidence = grading confidence, `occurred_at` = the submission time, model runs = the
    generation run (+ the evaluator run). It never goes through P3B attribution and claims no
    attribution, mapping, segment or raw message.
26. **Atomic completion.** Result + evidence + EVALUATED (+ the session's recommendation →
    COMPLETED) are one transaction; then the existing `recompute_ledger(conn, learner, [skill],
    policy=policy)` and `refresh_recommendations(conn, learner, policy=policy)`. A crash before the
    commit leaves nothing; a crash after it is repaired by the replay (`ALREADY_EVALUATED` runs the
    projections again). Replay creates 0 results, 0 evidence, 0 sessions / items.

## Mastery (ledger/p6-v1)

27. `VERIFICATION_SOURCES = {"VERIFICATION"}`; the algorithm version is `ledger/p6-v1`.
28. **VERIFIED is entered through the frozen gates.** §10.3 lists its thresholds as the *initial
    gate*: at a checkpoint (now, or the day of an earlier performance event) there is a successful
    verification at most 180 days old AND mean ≥ 0.80 AND support ≥ 4.0 on the evidence up to it.
    A pass alone never verifies a skill short of the mean / support gates (later independent
    evidence can complete them while the pass is recent).
29. **VERIFIED holds** until the anchoring verification is stale (> 180 days) or **materially
    contradicted**. A single isolated later failure lowers the mean — it is recorded, never
    hidden — but does not erase VERIFIED (§16 "One isolated failure … does not erase strong
    history"), even when it pulls the mean below the entry gate (explanation
    `VERIFICATION_HELD`).
30. **Materially contradicted (no frozen threshold → conservative, policy-configurable):** at least
    `min_contradicting_failures` (default **2**; the validator refuses fewer than 2) failures of
    an independent type (INDEPENDENT_APPLICATION, TRANSFER, EXECUTION_RESULT, VERIFICATION) with
    outcome INCORRECT after the last verified checkpoint. PARTIAL results and assisted attempts do
    not contradict.
31. **NEEDS_REVERIFICATION** = previously VERIFIED and (stale or contradicted), after the UNKNOWN
    check. A later SkillMirror check resolves it: it re-enters VERIFIED when the gates hold, and
    otherwise the regular evidence gates apply (a failed or sub-gate recheck is not kept in
    NEEDS_REVERIFICATION forever).
32. The rule is replay-deterministic (whole UTC days, chronological order by occurrence then id)
    and is part of the ledger's policy snapshot (`reverification`).

## Debt, recommendations, explanations

33. **Debt** is unchanged code: `verification_factor` already reads `is_verification()`. With
    VERIFICATION evidence: latest recent (30 d) check passed → 0.2, none → 0.6, failed → 1.0.
    Verification evidence is never a delegation, so a failed check cannot create eligibility.
34. **Engine 16 → `recommendations/p6-v1`:** REVERIFY names why (`VERIFICATION_STALE` /
    `VERIFICATION_CONTRADICTED`, derived by the mastery engine at the ledger row's
    `computed_as_of`). VERIFIED → NO_ACTION `RECENTLY_VERIFIED`. The version change supersedes
    ACTIVE rows once, on their next refresh.
35. **Explanations:** `RECENT_VERIFICATION`, `VERIFICATION_HELD`, `VERIFICATION_STALE`,
    `VERIFICATION_CONTRADICTED`; the VERIFIED entry gates (`RECENT_CHECK_PASSED`,
    `VERIFIED_RESULTS`, `VERIFIED_EVIDENCE`) are shown once the skill has a verification.

## API and web

36. `GET /v1/verifications` (queue groups: preparing, ready, in_progress, pending, completed,
    closed; the day's budget), `GET /v1/verifications/{id}` (additive: the page needs it after a
    reload), `POST …/start` (READY → IN_PROGRESS; again = resume, no new item),
    `POST …/submit` (Idempotency-Key; 202 stored, 200 replay, 409 same key + other answer /
    already submitted / not started, 422 answer does not fit), `POST …/abandon` (additive). Every
    endpoint filters by the JWT learner (a foreign session is 404) and makes no model call (a
    fresh-interpreter test proves the modules never load the gateway).
37. **Never returned:** expected answers, rubrics, validator reports, prompts, provider details.
    The answer key appears in the learner-facing feedback only after grading.
38. **Web:** `/verifications` (Verification Center, auto-refresh while preparing / grading) and
    `/verifications/[id]` (start, challenge, local draft that survives a reload or a dropped
    connection, one idempotency key per form, result and feedback). VERIFY / REVERIFY
    recommendations link to the open session or the center (dashboard, Skill Detail); the
    "Verification" section is in the navigation.

## Jobs, routing, budget

39. `GENERATE_VERIFICATION` and `GRADE_VERIFICATION` on `processing_jobs` (entity: the session),
    with the existing SKIP LOCKED claim, retries, backoff, stale-lock recovery and deferral. Final
    non-transient failures close the session honestly (`GENERATION_FAILED` / `GRADING_FAILED`).
40. Both tasks are **ROUTINE**: with `GEMINI_ROUTINE_MODEL=gemini-3.5-flash-lite` they use the
    500-RPD pool; the architecture default stays `gemini-3.7-flash`.

## Engineering defaults (policy `verification`; not frozen by the architecture)

| Key | Default | Frozen? |
| --- | --- | --- |
| `planner.max_daily_unsolicited` | 2 | Appendix B |
| `planner.cooldown_after_pass_days` | 7 | cooldown required by §11.1; the length is a default |
| `planner.cooldown_after_fail_days` / `_after_abandon_days` | 1 / 1 | default |
| `planner.retry_after_generation_failure_hours` | 24 | default |
| `planner.abandon_in_progress_after_days` | 14 | default |
| `difficulty.band_half_width`, `min`, `max` | 0.15, 0.1, 0.9 | default |
| `generation.max_attempts` | 3 (initial + 2) | §11.3 |
| `generation.supported_assessment_types` | mcq, numeric, short_response, reasoning | hackathon scope |
| `generation.history_limit`, `duplicate_max_similarity` | 5, 0.8 | default |
| `generation.min/max_prompt_chars`, `min/max_estimated_minutes` | 20 / 4000, 1 / 15 | default |
| `grading.numeric_relative_tolerance`, `_absolute_tolerance` | 0.005, 1e-6 | default |
| `grading.max_response_chars`, `short_response_max_chars`, `numeric_max_chars` | 4000, 1000, 64 | default |
| `grading.rubric_pass_min_score`, `min_ai_grading_confidence` | 0.7, 0.7 | default |
| `reverification.min_contradicting_failures` | 2 (≥ 2 enforced) | default (conservative) |
| `reverification.contradicting_evidence_types`, `_outcome_signals` | independent types, INCORRECT | default |
| `mastery.verified_min_mean`, `verified_min_support`, `verification_max_age_days` (0006) | 0.80, 4.0, 180 | Appendix B / default age |
| `debt.verification_factor` (0006) | 0.2 / 0.6 / 1.0 | §10.4 |

## Expected provider requests

| Work | Generation | Embedding |
| --- | --- | --- |
| `GET /v1/verifications` (planning), start, submit, abandon, any read | 0 | 0 |
| A verification challenge | 1 (+1 repair if schema-invalid; +1 per regeneration, ≤ 3 attempts) | 0 |
| Grading MCQ / numeric / exact short answer | 0 | 0 |
| Grading a free-text answer | 1 (+1 repair) | 0 |
| Replay of any P6 job | 0 (finished state or exact cache) | 0 |

## Validation (2026-09-25)

41. **Local:** pgTAP 288 (0008: 79); backend 747 unit / 879 with PostgreSQL; web vitest 80;
    extension 78 + 3 Chromium; safety benchmark 22/22, false debt 0. `scripts/acceptance_p6.py`
    (full local stack, scripted fake provider, API without a Gemini key): **15/15**, plus the
    browser walkthrough `e2e/p6-acceptance.spec.ts` (recommendation → center → challenge →
    reload/resume → submit → result → Skill Detail VERIFIED). The hosted script
    (`scripts/acceptance_p6_hosted.py`) was rehearsed locally on both fixture paths.
42. **Hosted:** 0008 was pushed on 2026-09-25 with the owner's approval ("0008 only") and
    verified read-only before any acceptance: local = remote 0001–0008; the new tables and enums
    exist; `verification_items` has no client grant or policy; `authenticated` has SELECT only
    on sessions and results, `anon` nothing; the 0006 constraint is gone; the policy key equals
    the seed; every existing row is unchanged. The hosted real acceptance then **passed on
    `gemini-3.5-flash-lite`** with 1 generation, 0 evaluation and 0 embedding requests: 12/12
    checks + the browser walkthrough. It is the **REAL LIVE PROOF** case: a real Gemini
    challenge (MCQ, attempt 1, all validator checks true) → real submission → deterministic
    grading → one real VERIFICATION EvidenceEvent → ledger DEVELOPING → VERIFIED (mean 0.780 →
    0.835, support 2.55 → 4.05, recent pass: every gate met by the live pass itself) → debt
    15.31 → 2.50 (factor 0.6 → 0.2) → VERIFY COMPLETED. No evidence was added after the live
    pass. The disposable learner was deleted through the Auth cascade and the real learner's
    rows were unchanged. Details: `docs/project-state.md`, *Hosted acceptance P6*.

## Known limitations

- A free-text answer key's semantic correctness cannot be proven deterministically; MCQ / numeric
  keys are structurally checked, free text is graded against the rubric.
- Duplicate detection is lexical (word Jaccard + number masking); a paraphrase with different
  vocabulary passes. History is bounded (5 prompts).
- The daily budget counts sessions by learner-day; a timezone change mid-day can shift one day.
- A PLANNED session whose recommendation was superseded still issues its challenge (evidence from
  it is valid either way).
- One item per session in V1; adaptive difficulty and multi-item checks are later work.
- Contradiction counts only INCORRECT independent results; a long drift of PARTIAL results lowers
  the mean without triggering re-verification.
- The debt factor "due" case of §10.4 (1.0 when a check is due) is represented by
  NEEDS_REVERIFICATION → REVERIFY, not by the factor.
- Live coverage is one deterministically graded MCQ. The rubric evaluator, a regeneration after a
  validator rejection and a live FAILED result are proven with scripted providers only.
