# 0008 — P7 teacher + admin operations; P8 benchmark + hardening

- Status: accepted for P7 (validated locally; the hosted push of migration 0009 waits for the
  owner's approval). The P8 part is appended when P8 is implemented.
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

The copy guard also sees the conversation's earlier assistant messages (up to 50, not only the
4-message window) and matches an elided span piece by piece (`evidence/p8-v1`, H8).

Hosted remediation (not applied; needs the owner): the hosted runtime runs `main` without these
fixes, so a recompute now would be undone by its next recompute of the skill. After the merge and
a restart on the fixed code, `scripts/recompute_skill.py --learner 8afbd2c8-… --skill 2761328b-…`
(dry run by default; read-only dry run on 2026-09-25: debt eligible → not eligible, 30.1 → 0.0,
delegations 2 → 1, `ledger/p8-v1`) and then `--apply`, which also reconciles the recommendations
(VERIFY → superseded). The READY check `3aa4481e…` is not cancelled by any rule; leaving it lets
the learner add genuine verification evidence. The lost explanation cannot be restored: evidence
and attributions are immutable and the segment is already attributed.

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
