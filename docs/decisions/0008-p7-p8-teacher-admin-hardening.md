# 0008 — P7 teacher + admin operations; P8 benchmark + hardening

- Status: accepted; P7 and P8 complete, merged in PR #8 (`main` `01e828c`). P7 is on hosted
  (0009 pushed with the owner's approval; hosted acceptance PASS). P8 (§27–43): the hosted E2E
  smoke on the merged code passed, the three benchmark runs are recorded on hosted and the
  2026-09-25 defect is remediated on hosted (§24–26), all with the owner's approval; the 3.7 canary
  was skipped by the owner. Details: `docs/project-state.md`, *Hosted E2E smoke P8*.
- Date: 2026-09-25
- Scope: P7 (architecture §4 roles, §7.1 `audit_events`, §8.2 candidate review, §12.3 teacher /
  admin, §13 `/v1/me`, `/v1/teacher`, `/v1/admin`, §17 `benchmark_runs`) and the P8 hardening
  items that touch P7 code (JWT clock skew, error redaction). P7 + P8 share this ADR and one
  migration, **0009**; P8 needs no migration of its own.
- These decisions record how the frozen architecture was implemented where it left a choice. Where
  it is explicit, it was followed. Additive choices are marked as such.

```
GET /v1/me                          profile role (database) -> capabilities (which links to show)
GET /v1/teacher/courses[/{id}/overview]
    TEACHER/ADMIN profile -> a TEACHER membership of that course (no ADMIN bypass: 404) ->
    cohort aggregates of the course's STUDENT members only; suppressed below min_cohort (>= 3)
/v1/admin/*  (ADMIN profile)
    reads: overview, jobs, model runs (+ budget), candidates, benchmark, course/skill lookup
    writes (Idempotency-Key -> one transaction -> audit event):
      POST jobs/{id}/retry                RETRY | RESUME_ATTRIBUTION
      POST skill-candidates/{id}/review   APPROVE | MERGE | REJECT   (+ EMBED_SKILL job)
      POST courses/{id}/members           STUDENT | TEACHER
No P7 endpoint makes a model call (test_zero_model_calls loads every P7 module gateway-free).
```

## Invariants this ADR enforces

| Invariant | Where it is enforced |
| --- | --- |
| The role is the database's | `app/auth/roles.py` reads `profiles.role` on every request; token claims (`user_metadata`, `app_metadata`) are ignored. Tests: forged metadata on a STUDENT → 403 on every teacher / admin route (DB matrix + local acceptance with a real `supabase.auth.updateUser`) |
| Unknown is not weak | The teacher view counts UNKNOWN (no ledger row, or support below the gate) as its own state, labelled "Not enough evidence yet", neutral grey, first in the legend; skills are ordered by topic / importance / name, never by a weakness measure |
| AI use is not dependency | Teachers see no AI-usage, per-actor or debt number. Evidence counts include only the students' own work (INDEPENDENT_EXPLANATION / _APPLICATION, TRANSFER, EXECUTION_RESULT, TEACHER_EVIDENCE) and VERIFICATION; ASSISTED_ATTEMPT, OBSERVATION and EXPOSURE are left out because a count of them would be an AI-usage measure |
| No individual student data for teachers | The overview payload has no learner id, name, e-mail, per-student row, mastery mean, debt or actor field (contract test + DB test + acceptance); the whole course is suppressed below `teacher_view.min_cohort`, whose floor 3 is also enforced in code (`TeacherViewPolicy`) |
| A teacher membership is never a learning context (N2) | Every student-context membership join uses `role = 'STUDENT'`: course list / detail, ledger scope and course filter, recommendation scope and filter, verification planner, skill detail, feedback scope, and the pipeline's `resolve_courses` |
| Admins operate the pipeline, not the learners' content | Admin payloads never carry `model_runs.output`, captured text, spans or prompts; job and model errors are redacted (API keys, JWTs, bearer tokens, connection strings, passwords, e-mail addresses) and truncated |
| Every admin mutation is idempotent and audited | `app/admin/audit.py`: an advisory lock on (actor, key), replay by the key's audit row (same request hash → the recorded result, another request → 409), the mutation, and the audit row, in one transaction |

## Schema (migration 0009)

1. **Numbering.** 0001–0008 are on hosted and unchanged: git blob ids pinned by
   `test_migrations_frozen.py` (0008 added); pgTAP 0009 pins the applied statements.
   *Finding:* the Supabase CLI records the migration file's bytes, and hosted **0002 and 0008 were
   pushed from CRLF working copies** (the SQL is identical). The pins therefore compare
   CR-normalized statements (hosted, a fresh LF checkout and CI agree on the values), the 0008
   pin was corrected the same way, the frozen-file test hashes the git-normalized content, and a
   new test requires the next migration (0009) to be LF on disk before it is pushed.
2. **Enums** (additive): `audit_actor_type` (USER, OPERATOR), `audit_action` (JOB_RETRY,
   JOB_RESUME_ATTRIBUTION, CANDIDATE_APPROVE, CANDIDATE_MERGE, CANDIDATE_REJECT,
   COURSE_MEMBER_ADD, ROLE_CHANGE), `benchmark_mode` (DETERMINISTIC, REPLAY, LIVE),
   `benchmark_verdict` (PASS, FAIL).
3. **`audit_events`** — actor (type, profile `set null`, role at the time), action, entity,
   metadata (ids / states / codes only), `client_request_id` + `request_hash` (unique per actor).
   A USER event always has a role and a key; an OPERATOR event has neither actor nor role.
   Append-only (only the actor's `set null` passes the guard). Server-only.
4. **`benchmark_runs`** — set name / version, mode, provider / model / routing (required unless
   deterministic), prompt versions, policy hash, code sha, case counts (passed + failed + blocked
   = cases), hard gates, calibration metrics, verdict, provider / embedding requests (0 unless
   LIVE), report (ids and codes), timing. Append-only, server-only. P7 reads it, P8 writes it.
5. **`processing_jobs`** — `manual_retry_count` (0–5, a check) and `last_manual_retry_at`. A guard
   lets the count grow by exactly one, only from FAILED (or COMPLETED for PROCESS_RAW_MESSAGE),
   only to PENDING with `attempts = 0`, unlocked; the worker's own updates cannot touch the
   fields. *The retrying admin is not stored here* (the table is learner-readable through RLS);
   it is in the audit event.
6. **`skill_candidates`** — `reviewed_by` (`set null`), `reviewed_at`, `review_note`; checks:
   PENDING_REVIEW ⇔ no review. A guard allows PENDING_REVIEW → APPROVED / MERGED / REJECTED once,
   by an ADMIN profile, changing only the status, resolution and review fields; APPROVED / MERGED
   require the candidate's name key to resolve to the resolved ACTIVE assessable skill (APPROVED:
   a node with source CANDIDATE_APPROVAL). A reviewed candidate is final; only `occurrences`
   grows. A partial unique index keeps one REJECTED row per name.
7. **`course_memberships`** — a TEACHER membership needs a TEACHER or ADMIN profile (trigger on
   insert / role change), and a profile holding TEACHER memberships cannot be demoted to STUDENT
   (trigger on `profiles.role`). Index (course, role).
8. **`skill_ledger (computed_as_of)`** index, for the P8 daily stale-ledger recompute.
9. **`policy_config.teacher_view`** = `{min_cohort: 3, window_days: 30, top_n: 10}` — its own
   loader; not one of the worker's `POLICY_KEYS`, so no worker depends on 0009.
10. **RLS.** Both new tables: RLS on, no grant, no policy. No teacher policy is added: through the
    Data API a teacher reads what any member reads (the course, its skills, their own membership
    row) and no other learner's row. pgTAP 0009 runs an **RLS sweep** over every public table: RLS
    enabled everywhere; `anon` has no privilege on any table or view; `authenticated` can write no
    table (profiles: two columns only); every client-readable table has a policy; the server-only
    tables have no grant and no policy; no SECURITY DEFINER function in `public` is executable by
    clients; and a TEACHER JWT (with a forged ADMIN metadata claim) reads none of the students'
    ledger / evidence / jobs / activity / feedback / verification rows.

## Roles and authorization

11. `profiles.role` is read per request (`require_actor`); `SignedInActor`, `TeachingActor`
    (TEACHER / ADMIN), `AdminActor` (ADMIN). Role changes are operator actions only:
    `scripts/grant_role.py` (DATABASE_URL), which writes the change and an OPERATOR `ROLE_CHANGE`
    audit event in one transaction. There is no role API.
12. Matrix (tested for every route): anonymous 401; STUDENT 403 on every teacher / admin route;
    TEACHER 403 on admin routes; the teacher overview requires a TEACHER membership of **that
    specific course** — anyone else gets **404** (existence hidden), an ADMIN profile included
    (owner correction, 2026-09-25: admin course access is the admin course lookup,
    `/v1/admin/courses[/{id}]`; an ADMIN who is also a TEACHER member is served like a teacher).
    ADMIN 200 on the admin routes. A malformed or foreign token is 401 before any database access.

## Teacher overview

13. **Scope** = the course's STUDENT members × its ACTIVE assessable skills. The ledger is per
    learner and skill (not per course context), so every count uses the same scope rather than
    the capture's course context.
14. **Counts.** Per skill: students per mastery state (they add up to the cohort; a missing ledger
    row is UNKNOWN), students with own-work evidence, students with an ACTIVE VERIFY / REVERIFY
    recommendation. Course: state totals over (student, skill) pairs; skills students worked on
    (distinct students with ACCEPTED, uncorrected mappings in `window_days`; *not* an interaction
    count); verification needs; own-work and verification evidence counts (all time and window);
    `as_of` = the oldest ledger computation read. Excluded evidence and mappings the learner
    corrected (WRONG_SKILL / DONT_COUNT) are never counted.
15. **Suppression.** Below `min_cohort` (≥ 3) the overview returns the cohort size only; the list
    marks the course suppressed.

## Admin operations

16. **Retry.** RETRY: a FAILED job → PENDING with a fresh attempt budget, at most 5 manual retries
    (the check and the guard of 0009), conditional on the inspected state under a row lock.
    Per type: BOOTSTRAP_COURSE_GRAPH first restores the graph stage (**N3**:
    `skill_graph.stages.prepare_bootstrap_retry` — EMBEDDING when the graph was canonicalized, so
    the retry makes 0 generation requests and no graph v2; PENDING otherwise). GENERATE / GRADE
    verification jobs are retried only while their session is still open; a session closed with
    its failure recorded (P6) is final and the planner plans a new one (409
    `VERIFICATION_CLOSED`). RESUME_ATTRIBUTION (**K7**): a COMPLETED raw-message job whose MAPPED
    task unit still has ACCEPTED mappings without attribution (turns analysed before 0005) →
    PENDING; the worker skips P3A and makes exactly one SKILL_ATTRIBUTION request per pending
    segment. The resumable job is the one that builds the unit (the assistant message's job for a
    paired turn — the user message's job deferred to it). Hosted resumption of the pre-0005 turns
    is an owner-approved action, not automatic.
17. **Candidate review.** APPROVE is the second controlled path to an ACTIVE node (§8.2): the name
    must not already resolve (409: merge instead); a description is required (the candidate's or
    the admin's); the node has source CANDIDATE_APPROVAL, kind SUBSKILL under an assessable parent
    (PARENT edge, cycle-checked) or SKILL; the course overlay goes to the candidate's first course
    unless another is named (importance: the admin's or `skill_graph.default_importance`).
    MERGE adds the name as a VARIANT alias (source CANDIDATE_APPROVAL) of an ACTIVE assessable
    target. REJECT keeps the name out; **N4**: a later proposal of a rejected name counts on the
    REJECTED row (`processing/persist.py`) and never reopens review. APPROVE and MERGE enqueue — or
    re-arm a finished — `EMBED_SKILL` job (entity: the skill node; `queue.enqueue_or_rearm_job`),
    so retrieval finds the skill by its new text; the embedding runs in the worker. Earlier turns
    are not remapped.
18. **Enrollment.** `POST /v1/admin/courses/{id}/members` adds an existing account (by e-mail or
    id) as STUDENT or TEACHER; a different existing role is 409; a TEACHER membership for a
    STUDENT profile is 422 (the 0009 guard).
19. **Reads.** Jobs (state / type filters, keyset pages, the allowed action or the blocking code),
    model runs (no output; 24-hour status counts; the request budget of the quota day re-derived
    from `model_runs` against the configured limits), candidates (with lexically similar skills),
    benchmark runs, course / skill lookup, recent audit events.
20. **Redaction** (`app/core/redaction.py`) applies when job and graph errors are stored (they are
    learner-readable) and to everything the admin API returns.

## Hardening done in P7 (P8 items)

21. **JWT clock skew.** `SupabaseJWTVerifier` accepts a 5 s leeway (bounded to 0–30 s) on `iat` /
    `nbf` / `exp`, fixing the hosted P6 "token is not yet valid (iat)" 401 when the local clock
    trails Supabase Auth. An expired token (beyond 5 s) stays expired.
22. **Gateway-free versions.** `app/intelligence/versions.py` holds ANALYSIS_VERSION /
    ATTRIBUTION_VERSION (re-exported by the persist modules) so model-free readers never import
    the engines.

## Attribution / evidence consistency (hosted defect, 2026-09-25)

A hosted learner turn (skill "Writing for loops over ranges", 16:43 UTC) showed, for one mapped
skill, "Actor: You" and "The AI did it" in the activity feed, "no independent evidence yet" in the
skill view, a high reliance signal, VERIFY and a READY check. Traced read-only through every table
(ids only in this record: learner `8afbd2c8…`, conversation `c4f799a1…`, messages `ba691ad3…` /
`c139b497…`, segment `0d499b76…`, mapping `aeac8331…`, attribution `114b7671…`, evidence
`91b0d433…`, recommendation `e8d6092f…`, verification session `3aa4481e…`):

- The learner wrote a canonical two-line loop plus their own explanation of it and asked the AI to
  check the explanation. The same loop was in the AI's answer to the learner's earlier "how to
  write a for loop" turn (16:05, inside the recent context window).
- **P3A** mapped it correctly (MAP, 0.95). **The attributor** (`skill-attribution/v1`) said
  STUDENT / INDEPENDENT_APPLICATION / CORRECT, quoting the loop. **The qualification's copy guard**
  found that quote in the earlier AI answer and recorded the evidence as AI / OBSERVATION /
  `COPIED_FROM_AI` (strength 0). The paired confirmation reply played no part (it is after the
  learner message; a replay of the qualification without the 16:05 answer gives STUDENT, 0.83).

Three defects, all fixed (regression suite `tests/test_attribution_consistency_db.py`, each fix
mutation-checked):

24. **Activity actor ≠ evidence actor.** The activity chip showed the attributor's claim
    (`attributions.actor`) beside the recorded evidence type. `GET /v1/activity` now returns the
    recorded evidence's actor as `actor` (the value the skill timeline and the ledger use), the
    claim as `attributed_actor`, and the `qualification_reason`; the web chip and the skill
    timeline's "Why?" explain a reclassification ("matches an earlier AI answer in this
    conversation"). The attribution row keeps the attributor's claim: it is immutable provenance.
25. **A copy-guard reclassification counted as a second delegation** (`ledger/p8-v1`). The AI's
    one answer was counted when it was produced (the 16:04 turn) and again when the learner reused
    it, so one AI interaction made the skill debt-eligible (score 30.1, VERIFY, a planned check):
    a false AI Assistance Debt. `is_delegation` now ignores evidence whose qualification reason is
    `COPIED_FROM_AI`; the ledger carries `qualification_reason` for this. Missing a debt is the
    safe direction.
26. **The learner's own explanation was lost** (`skill-attribution/v2`, `attributor/p8-v1`). The
    attributor was not told which text was reused, so it quoted the loop; one span per skill meant
    the explanation never became evidence. The request now lists "Reused assistant text": the parts
    of the learner message found (deterministically, `reused_ai_text`) in the conversation's earlier
    assistant messages. The span rules say never to quote them, to quote the learner's own
    explanation or reasoning instead (with its evidence type), and that asking the AI to check the
    learner's work does not make the AI the actor. The copy guard stays the deterministic safety
    net. Live check on `gemini-3.5-flash-lite` (synthetic text, 3 requests): own work without an
    earlier example → STUDENT / INDEPENDENT_APPLICATION; the live shape → STUDENT /
    INDEPENDENT_EXPLANATION quoting the explanation (strength 0.50); "how do I write a for loop?" →
    AI / OBSERVATION, no mastery. The v1 prompt on the same synthetic input (1 request) reproduced
    the hosted result (STUDENT quoting the loop → `COPIED_FROM_AI`). The idempotency key
    (`p3b-v1`) is unchanged, so attributed segments are never re-attributed.
    **The prompt alone was not enough:** on the critical gate's case of the same shape,
    Flash-Lite under v2 still quoted the reused loop. So the attribution validator now applies the
    copy guard's own rule before an answer is accepted: a STUDENT / SHARED performance span found
    in the conversation's earlier assistant output is invalid output, with a repair message naming
    the fix; one repair, then abstention (no evidence). Cached outputs are re-validated. Live, the
    repair quoted the learner's explanation (STUDENT / INDEPENDENT_EXPLANATION). The qualification's
    copy guard stays as the safety net and keeps its meaning for rows written before.

The copy guard also sees the conversation's earlier assistant messages (up to 50, not only the
4-message window) and matches an elided span piece by piece (`evidence/p8-v1`, H8).

Hosted remediation (**complete**, 2026-09-25 20:06 UTC, after the merge): the owner ran the
targeted `scripts/recompute_skill.py --learner 8afbd2c8-… --skill 2761328b-… --apply`. It does
for this one row what the worker's startup sweep (§37) does for every stale row. It was chosen
over the sweep because a rolled-back preview showed the sweep would also re-tag the real P3B/P4
learner's ledger row and insert their recommendations. Result, checked read-only by
`scripts/smoke_p8_hosted.py affected` (8/8):
- delegations 2 → 1 and debt 30.1433 → 0 (not eligible), `ledger/p8-v1`; the VERIFY superseded,
  NO_ACTION active, no new VERIFY;
- activity actor = evidence actor;
- only the ledger and recommendations changed: 0 model runs and 0 registry, evidence, attribution
  or raw rows were created.

The READY check `3aa4481e…` is not cancelled by any rule and was left untouched; how the UI
presents a READY check whose VERIFY is superseded is a P9 UX item. The lost explanation cannot be
restored: evidence and attributions are immutable and the segment is already attributed.

## P8: the critical gate

27. **120 labeled cases** in ten families (REL 14, SEG 8, MAP 16, ATT 16, EVM 10, DEBT 18, ABS 10,
    ADV 10, VER 10, GRD 8; 72 live-capable) in `benchmark/cases/critical-gate/`. The 34 earlier
    cases are referenced by `source` and read from their original files unchanged (12
    `p3a-smoke`, 22 `p3b-p4-safety`); 86 are new, synthetic text only. `validate` checks the exact
    taxonomy (CI).
28. **Fixture graphs** (`benchmark/fixtures/critical-gate-courses.json`): 6 courses (Python, SQL,
    Business Math, Statistics, Physics, Academic Writing), 16 topics, 42 skills incl. one SUBSKILL
    (for the hierarchy rule), hand-written and frozen, ids `uuid5(key)`. Seeded into a **local / CI
    database only**; the runner refuses any other host (`assert_local_database`), since fixture
    nodes would enter real learners' retrieval.
29. **Case kinds.** *turn* runs the production pipeline: a fresh learner and STUDENT membership,
    ingestion as the `chatgpt-2` adapter, `process_raw_message_job` (P3A, P3B, P4, P5), then the
    activity feed, evidence, ledger and recommendations are read back; every job is replayed
    (must be 0 provider requests and 0 new rows). *evidence* scripts attribution outputs through the
    qualification, mastery and debt engines (plus P5 exclusion and P6 verification evidence);
    *verification* runs the generator and the deterministic validator (code / sql must be refused
    as SANDBOX_UNAVAILABLE and never delivered); *grading* runs response validation, the
    deterministic graders and the rubric evaluator.
30. **Modes.** *deterministic* (CI, all 120): a scripted model answers each case; ADV cases and
    the guard cases script a *compromised* twin that the deterministic layer must neutralise.
    *live* (by hand): the 72 live-capable cases on the operational model, all generation on it,
    exact cache, RPM 12, `--max-requests` required (Google's quota is shared with hosted; the run
    refuses a plan above it and blocks a case the remaining cap cannot finish), transient
    provider errors retried per case. *replay* (CI): the 72 cases from the recording.
31. **Record / replay** (`app/model_gateway/replay.py`, H2/N5): keyed by a sha256 of everything
    the provider sees; embeddings recorded too; a replay miss is a *stale recording* and fails the
    case. The benchmark pool sets `hnsw.ef_search=1000` so vector retrieval is exact.
32. **Hard gates** (zero tolerance, every mode): false AI Assistance Debt; debt eligibility from a
    single interaction; UNKNOWN presented as weak (a mean, PRACTICE or PREREQUISITE); mastery from
    exposure / observation; VERIFIED without verification; invented / non-candidate skill ids;
    successful prompt injection; evidence on must-abstain cases; replay duplicates; invalid
    verification items delivered; deterministic grader accuracy < 100%; and **the attribution /
    evidence consistency gate** (§24): every activity chip's actor, reason and type equal the
    recorded evidence's.
33. **Calibration targets** (reported, never a gate): relevance precision 0.90 / recall 0.85,
    skill-bearing 0.85 / 0.80, top-1 mapping 0.75, top-3 0.90, actor 0.85, evidence type 0.75,
    abstention precision 0.90, verification acceptance 0.80, rubric grading 0.85, debt recall 2/3.
    A change of a target needs a note here and a new baseline (CI checks the baseline's targets).
34. **Regression baseline** (H16, `benchmark/baselines/<model>.json`): written only from a
    complete, passing live run; holds the prompt versions, the recording's sha256 and the metrics.
    CI fails when an engine prompt version changed without a new recording, the recording no
    longer matches, or a replayed metric falls more than 5 points below the baseline.
35. **Results.**
    - *Deterministic* (CI): **120/120 PASS**, every hard gate 0, grader accuracy 100%, 0 provider
      requests.
    - *Live* (2026-09-25 18:12–18:44 UTC, local database, `gemini-3.5-flash-lite` for every
      generation task, RPM 12, cap 240): **72 cases, 71 passed, 0 blocked, every hard gate 0**;
      115 generation + 68 embedding requests. REL-06 failed on repeated provider transport errors
      after its retries. An earlier attempt was stopped after 23 HTTP 503s in 15 minutes (Google
      "high demand"); its answers stayed in the local durable cache, so REL-01..04 were never
      recorded. Those four and REL-06 were re-recorded with `--fresh`: 5/5, 6 generation + 7
      embedding requests.
    - *Replay* of the committed recording (193 entries): **72/72 PASS**, 0 provider requests,
      every hard gate 0. These metrics are the baseline (`measured_by: replay`).
    - *Calibration* (baseline vs target): relevance precision 1.00 / recall 0.875 (0.90 / 0.85),
      skill-bearing 1.00 / 0.933 (0.85 / 0.80), top-1 1.00 (0.75), top-3 1.00 (0.90), actor 0.923
      (0.85), evidence type 0.85 (0.75), verification acceptance 1.00 (0.80), rubric grading 1.00
      (0.85), debt recall 1.00 (2/3). Abstention precision is 0.0 on **one** sample: the only
      MAP-routed abstention was ADV-05 (the delimiter-escape injection turn, CANDIDATES_REJECTED),
      the safe direction; the metric is not informative at this size.
    - *Soft misses* (the model's calibration, no gate touched): Flash-Lite credited a question as
      the learner's own performance in SEG-06 ("what is 12% of 250" → STUDENT /
      INDEPENDENT_APPLICATION) and, arguably, SEG-01, MAP-13, MAP-16 (reasoning inside a question);
      MAP-04 mapped the unknown polars library to the SQL GROUP BY skill (EXPOSURE only) instead of
      proposing a candidate; MAP-08 ("What is a regression?") and ATT-02 ("just give me the code")
      were routed STOP; MAP-16 was accepted at first pass (≥ 0.80), so adjudication still ran only
      scripted (K25); ATT-07 SHARED was read as STUDENT, ATT-11 TRANSFER as INDEPENDENT_APPLICATION.
      None creates debt; the false credits raise mastery a little (strength ≤ 0.9 each). They are
      the input of a future `skill-attribution/v3` (K4), not fixed by lowering a gate.
    - *Record*: `benchmark_runs` on the local database holds the four rows (live 71/72 FAIL, the
      fresh re-record 5/5, replay 72/72, deterministic 120/120). On hosted (owner-approved,
      2026-09-25), `critical_gate.py record --from-report … --record-to <hosted>` inserted the three
      saved reports unchanged: deterministic 120/120 PASS, replay 72/72 PASS, live 71/72 FAIL
      (every hard gate held). Each hosted row equals its report field by field, and
      `/admin/benchmark` shows them. The fresh re-record 5/5 stays local.

## P8: hardening

36. **Budget fail-safe** (H6, N1): the default daily limits cover every model this project uses
    (3.7 / 3.8 flash 20, Flash-Lite 500, embedding 1000); startup fails when the generation or
    routine model has no limit, unless the budget is explicitly `off`.
37. **Stale-ledger sweep** (H7, K1): the worker, at startup and once per UTC day, re-derives ledger
    rows computed before today (UTC) or by an older algorithm (`recompute_ledger` +
    `refresh_recommendations`; no model call). Unchanged rows only advance `computed_as_of`; a full
    batch continues on the next poll. After a deployment, rows of the previous algorithm are
    re-derived automatically, which remediates the hosted false-debt row once the fixed code runs.
38. **Copy guard** (H8, K2): see §24–26 (`evidence/p8-v1`, conversation-wide history of up to 50
    assistant messages; the attribution validator applies the same rule before the model's answer
    is accepted). Pastes from other conversations stay undetected.
39. **Invented-id crash** (found by the gate): a TURN_ANALYSIS mapping of an id outside the
    candidate pool that the model did not rank entered the top-K as an implicit rank and crashed the
    job (KeyError / ValueError); validation now rejects it (one repair, then the explicit
    abstention).
40. **Robustness tests** (H3–H5, H15): a crash before P3A commits leaves nothing and the retry runs
    once; a crash between evidence and ledger is repaired by the retry with no model call; a
    correction interrupted mid-recompute is all or nothing and retries once under its key; a replay
    after a correction never brings the evidence back; every job type defers 429 / 503 / budget
    without spending an attempt, the retry hint clamped to 15–600 s (budget ≤ 1 h); the cache
    records its source run, re-checks cached outputs against the current validator, never caches a
    failure and misses on a prompt-version bump.
41. **Extension** (H9 baseline, H11, H12): the hotfix's `chatgpt-2` adapter and capture-degraded
    status are the baseline (not reimplemented). The fixture matrix on the signed-in thread layout
    covers a regenerated answer, an edited question, an attachment, rendered math, a message over
    100k characters and an unreadable layout. It found that KaTeX math was captured as MathML
    tokens plus the TeX source; it is now the TeX source once (DOM contract unchanged, adapter
    version still `chatgpt-2`). The popup gains the **active-course picker** (Auto or one studied
    course); the service worker binds it to each envelope, content scripts never choose, the
    backend still re-validates `active_course_id`.
42. **Security sweep** (H14): `model_runs.error_message` is redacted when written; a guard test in
    the web app and the extension forbids raw-HTML sinks in product code; `npm audit` (production
    and dev) and `pip-audit` report no known vulnerability (report-only).
43. **Not done in P8** (recorded, not claimed): the recorded signed-in ChatGPT fixture and the
    `SIGNED_IN=1` live check (H10: needs the owner signed in; the hotfix's structure-from-live
    fixture and the hosted `chatgpt-2` captures stand in); the E2E CI job on the replay provider
    (H13); the 3.7 canary (skipped by the owner). The hosted E2E smoke was run after the merge
    with the owner's approval and passed: `scripts/smoke_p8_hosted.py` + `e2e/p8-smoke.spec.ts`,
    a disposable learner on Flash-Lite (5 generation requests; the attribution validator refused
    the reused-loop span live and the repair credited the learner's explanation), cleaned up
    through the Auth cascade.

## Web

23. `/teacher`, `/teacher/courses/[id]`, `/admin`, `/admin/jobs`, `/admin/model-runs`,
    `/admin/skill-candidates`, `/admin/benchmark`, behind the session proxy. Links from
    `GET /v1/me` (dashboard); the backend is the only authority and a 403 renders a plain panel.
    Mutating forms carry an Idempotency-Key minted when the page is rendered, so a double submit
    replays instead of repeating.

## Validation (local, 2026-09-25)

- pgTAP **363** (0009: 75, incl. the RLS sweep); backend **817** unit + **969** with the local
  database; web vitest **96**, lint, typecheck, production build; extension typecheck, 78 unit,
  build + manifest, Chromium 3.
- Local P7 acceptance (`scripts/acceptance_p7.py --local-graph` + `e2e/p7-acceptance.spec.ts`,
  real local Auth JWTs, API without a Gemini key): prepare 4/4, browser 3/3, verify 16/16,
  cleanup 3/3; no model run belonged to the run.

- P8 (local, 2026-09-26): backend 874 unit + **1046** with the local database (critical gate
  deterministic 120/120 and replay 72/72 included); pgTAP 363; web vitest 106; extension 100 unit +
  4 Chromium; `supabase db lint` no schema errors.

## Known limitations

- Aggregates can in principle be differenced (e.g. a one-student change between two views); the
  course-level cohort floor is the protection, not per-cell noise.
- The teacher view reads the ledger as stored; a row decays only when recomputed (P8 adds the
  daily recompute).
- The teacher endpoints serve only courses with a TEACHER membership; an ADMIN inspects other
  courses through the admin course lookup (members, counts), which shows no cohort aggregates.
- Candidate APPROVE does not remap earlier turns and cannot rename the candidate; MERGE does not
  merge two existing nodes (that stays `registry.merge_skill`, operator-side).
- Audit events outlive deleted accounts with a null actor; nothing deletes them.
- P8: one learner span per (segment, skill). A learner who reuses AI output and adds their own work
  gets credit only for the part the attributor quotes; if it keeps quoting the reused part, the
  segment abstains (no evidence), the safe direction.
- P8: the copy guard compares normalized text only (whitespace, case, quotes). A reformatted or
  paraphrased copy, or a paste from another conversation, is not detected; span grounding stays
  lexical, so a paraphrased learner span abstains (K3; measured by ABS paraphrased-span).
- P8: the critical gate's live half ran once, on gemini-3.5-flash-lite; 3.7 / 3.8 have no live
  gate (20 requests a day). The fixture graphs are hand-written, not model-generated.
- P8: a live run shares Google's daily quota with hosted; `--max-requests` is the operator's
  estimate of what is left (the local and hosted budgets cannot see each other).
