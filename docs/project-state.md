# SkillMirror — Live Project State

This record carries live implementation status (architecture §0.1). It must never
claim an unverified gate. Architecture: [`architecture/`](architecture/). Decisions:
[`decisions/`](decisions/) (0001 P0, 0002 P1, 0003 P2 + P3A, 0004 free-tier ModelGateway,
0005 P3B + P4, 0006 P5, 0007 P6, 0008 P7 + P8).

**Last updated:** 2026-09-26

## Hackathon runtime decision (ADR 0003 §1)

SkillMirror is a **local-first hackathon application**. There is no Vercel/Render
deployment, production domain, cloud deployment pipeline or production SMTP.

```
unpacked Chrome extension → local Next.js :3000 → local FastAPI :8000 (+ in-process worker)
                                                    → hosted Supabase (Auth, Postgres, pgvector)
                                                    → Google AI API (Gemini)
```

The old P9 "Deployment + release" is replaced by **P9 — Local Demo Integration + Final Hardening**.

## Repository

| Field | Value |
| --- | --- |
| Repository | https://github.com/shaka9lakaboom/lappu1 |
| Default branch | `main`. P0, P1, P2 + P3A, P3B + P4, P5, P6, the demo-runtime hotfix and P7 + P8 are merged. **`main` = `01e828c2cff800a86f8bfa4bdb97954652c75d4e`** (merge of PR #8: P7 teacher/admin + P8 benchmark/hardening). |
| P7 + P8 merge commit | `01e828c2cff800a86f8bfa4bdb97954652c75d4e` (PR #8, head `f476bf0`; CI green on the PR run [36177071898](https://github.com/shaka9lakaboom/lappu1/actions/runs/36177071898) and on the `main` push [36177455267](https://github.com/shaka9lakaboom/lappu1/actions/runs/36177455267)) |
| Demo-runtime hotfix merge commit | `00ff0a40ea2f93438b70dc00962c9235721df325` (PR #7: demo runtime + the `chatgpt-2` ChatGPT adapter with capture-degraded status) |
| Hosted migrations | **0001–0009** (0009 = P7 + P8, pushed 2026-09-25 with the owner's approval). No migration was pushed after that; the next new one is 0010. |
| P1 merge commit | `5ee62d01cb40ffac3dbf9342456935092a944098` (PR #2) |
| P2 + P3A merge commit | `08260a397fb5b41d709b3c4074836cc7e21646da` (PR #3; its head `09386c2` had 7/7 CI jobs green before the merge) |
| P3B + P4 merge commit | `ab2d73d6e43f3c0122a08506ffedcd62c5ff19a8` (PR #4, head `c7aba9a`) |
| P5 merge commit | `a0e6982546c82bb4dbb1920894fb6d4711ccfa34` (PR #5, head `0f12dc7`) |
| P6 merge commit | `4a648ac2100738a228b3506cfb21e734c6756f02` (PR #6, head `583aea4`; the PR-triggered CI on that head passed before the merge) |
| Development branch | `skillmirror-p9-final-demo-hardening` (from `main` `01e828c`): so far only the P8 hosted smoke (script, walkthrough, records). P9 is not started. |
| Previous branch | `skillmirror-p7-p8-teacher-admin-hardening` (P7 + P8, from `4a648ac`; `main` `00ff0a4` merged in, no rebase; merged in PR #8) |
| P3B + P4 commits (merged) | migrations 0005 + 0006 · P3B attribution + evidence qualification · P4 mastery + debt + ledger · pipeline stage + `GET /v1/ledger` · contracts · safety benchmark · ADR 0005 + acceptance script · evidence sources fix (`1352b5b`) · day-granular recency + this record |
| P2 + P3A branch started from `main` | `5ee62d01cb40ffac3dbf9342456935092a944098` |
| P2 + P3A commits (merged) | `508ec0b` migration 0003 · `76483c8` model_runs FK-null fix · `f160a32` ModelGateway + policy · `9550838` courses API + skill graph · `f6fb81b` P3A pipeline + worker · `41472e3` contracts · `a9d7720` web course flow · `07a4a10` benchmark smoke set · `c723960` ADR 0003 + CI + env · `2801ed4` format fix · `5ddd71c` provider-schema allowlist · `bb2448f` state + acceptance script · `20dbd77` live Gemini fixes (schema limits, quotas, overload) · `19bd72f` partial live acceptance record · then the ADR 0004 series: migration 0004 · free-tier ModelGateway + combined turn analysis · ADR 0004 + this record |
| CI (P3B + P4) | green on the PR #4 head before the merge |
| CI (P2 + P3A) | green on `2801ed4` ([36071071084](https://github.com/shaka9lakaboom/lappu1/actions/runs/36071071084)) and `bb2448f` ([36071473581](https://github.com/shaka9lakaboom/lappu1/actions/runs/36071473581)), 7/7 jobs each. The ADR 0004 series is checked in the pull request (not yet run when this record was written). |
| Pull request | P7 + P8: PR #8, **merged** by the owner as `01e828c`. P6: PR #6, **merged** as `4a648ac`. P5: PR #5, **merged** as `a0e6982`. P3B + P4: PR #4, **merged** as `ab2d73d`. P2 + P3A: PR #3, **merged** as `08260a3` |

## Phase

**Phase: P7 + P8 — Teacher/Admin + Benchmark/Hardening. P7 COMPLETE. P8 COMPLETE.** Merged in
PR #8 as `main` `01e828c`. Hosted has **0001–0009** (P7 is migration `0009_teacher_admin_ops.sql`;
P8 needs no migration of its own, its storage `benchmark_runs` is part of 0009). Decisions:
[ADR 0008](decisions/0008-p7-p8-teacher-admin-hardening.md). **Next phase: P9 — Local Demo
Integration + Final Hardening, not started.**

- **P7:** migration 0009 pushed on 2026-09-25 with the owner's explicit approval ("Approved: 0009
  only") and verified read-only; hosted P7 acceptance PASS (see *Hosted acceptance P7*).
- **P8:** critical gate deterministic 120/120 and replay 72/72 (CI), live Flash-Lite 71/72 with
  every hard gate held; the three runs are recorded on hosted `benchmark_runs` and shown by
  `/admin/benchmark`; the hosted E2E smoke on the merged code PASSED (see *Hosted E2E smoke P8*).
  The `gemini-3.7-flash` canary was skipped by the owner.
- **Hosted remediation of the 2026-09-25 defect: COMPLETE.** The owner ran the targeted
  `recompute_skill.py --apply` (2026-09-25 20:06:05 UTC). The smoke's `affected` phase passes
  8/8, read-only: false delegations 2 → 1, debt 30.1433 → 0, the false VERIFY superseded,
  activity actor = evidence actor, 0 model runs and 0 registry / evidence rows created (see
  *Hosted E2E smoke P8*).
- **P9 UX cleanup item:** the historical READY check `3aa4481e…` of that learner still shows in
  the verification centre, although its VERIFY is superseded (no rule closes a READY session;
  it was left untouched on purpose).

```
GET /v1/me · GET /v1/teacher/courses[/{id}/overview]   (profile role from the database)
/v1/admin: overview · jobs (+ POST retry: RETRY | RESUME_ATTRIBUTION) · model-runs · benchmark
           skill-candidates (+ POST review: APPROVE | MERGE | REJECT) · courses (+ POST members) · skills
every admin mutation: Idempotency-Key -> one transaction -> audit event; no model call anywhere
```

| Gate | State | Evidence |
| --- | --- | --- |
| Migration 0009 applies from a clean reset; 0001–0008 unchanged | PASS (local) | `supabase db reset` 0001–0009; pgTAP pins the CR-normalized applied statements of 0001–0008, equal to the hosted ones (read-only query); git blob ids pinned (0008 added) |
| pgTAP 0009 (tables, enums, guards, retry / review / membership rules, RLS sweep over every public table, teacher JWT) | PASS: 75 | `supabase/tests/0009_teacher_admin_ops.test.sql`; total **363** |
| Roles from `profiles.role`, not token metadata; the teacher overview needs a TEACHER membership of that course (ADMIN without it: 404) | PASS | DB matrix (16 routes × student / forged metadata / teacher / admin); `test_authorization_uses_the_profile_role_and_the_membership`; local acceptance V1–V4 with a real `updateUser` metadata forgery |
| Teacher overview: exact aggregates, UNKNOWN neutral, no per-student / name / AI-usage / debt data, cohort ≥ 3 | PASS | `test_teacher_db.py` (3 seeded students: exact per-skill states, totals, evidence 27, needs, mapped skills; corrections drop out; suppression); contract tests; acceptance V5–V8 |
| N2: STUDENT memberships only for the learning context | PASS | `test_a_teacher_membership_is_never_a_learning_context`; acceptance V9 |
| Retry: FAILED → PENDING, audited, idempotent, cap 5; bootstrap resumes at EMBEDDING (N3, 0 generation); closed verification refused; RESUME_ATTRIBUTION = 1 request, no duplicates (K7) | PASS | `test_admin_db.py` (mutation-checked); acceptance V10–V12 |
| Candidate review APPROVE / MERGE / REJECT; approved skill embedded and retrievable; rejected name never returns (N4) | PASS | `test_admin_db.py` (mutation-checked); acceptance V12–V13 |
| Enrollment + teacher-membership guard; audit trail | PASS | `test_admin_db.py`; acceptance V14–V15 |
| Redaction; model runs without output (N6) | PASS | `test_p7_unit.py`, `test_admin_db.py`; acceptance V10 + browser |
| Zero model calls | PASS | `test_zero_model_calls` loads every P7 module gateway-free; acceptance V16 (no model run belongs to the run) |
| Web pages | PASS | vitest 96, lint, typecheck, build; local browser walkthrough 3/3 |
| Local P7 acceptance | **PASS**: prepare 4/4 · browser 3/3 · verify 16/16 · cleanup 3/3 | `scripts/acceptance_p7.py --local-graph`, `apps/web/e2e/p7-acceptance.spec.ts` |
| Hosted migration 0009 | **PASS**: pushed with the owner's approval; 18 catalog checks; data unchanged | See *Database* |
| Hosted P7 acceptance (disposable accounts, existing course 9440004a, no shared rows, 0 model calls) | **PASS**: prepare 4/4 · browser 3/3 · verify 17/17 · cleanup 5/5 | See *Hosted acceptance P7* |

### P8: benchmark + hardening (merged in PR #8; ADR 0008 §27–43)

| Gate | State | Evidence |
| --- | --- | --- |
| 120 labeled cases, exact taxonomy (34 migrated unchanged + 86 new; 72 live-capable) | PASS | `critical_gate.py validate`; `test_the_case_set_is_the_planned_taxonomy` |
| Deterministic 120/120, every hard gate 0, grader accuracy 100%, 0 provider requests | PASS | `test_all_120_cases_pass_deterministically_on_the_database` (CI) |
| Live 72 on `gemini-3.5-flash-lite` (bounded, recorded) | **PASS on every hard gate**: 71/72, 0 blocked; REL-06 hit provider transport errors, re-recorded 5/5 | ADR 0008 §35; local `benchmark_runs`; 115 + 6 generation, 68 + 7 embedding requests on 2026-09-25 |
| Replay 72 from the recording (CI; a miss = stale recording) | **PASS**: 72/72, 0 provider requests | `test_the_replay_set_passes_from_the_recording` |
| Regression baseline (prompt versions, recording hash, metrics; −5 pt tolerance) | PASS (measured by the replay) | `benchmark/baselines/gemini-3.5-flash-lite.json`; `test_the_committed_recording_fits_this_code` |
| Attribution / evidence consistency (hosted defect 2026-09-25) | PASS (merged; live on hosted with disposable data; the hosted row remediated) | §24–26; `test_attribution_consistency_db.py`; hard gate in the critical gate; *Hosted E2E smoke P8* |
| Hosted `benchmark_runs` (the three saved reports, inserted unchanged) | **PASS**: DETERMINISTIC 120/120 PASS · REPLAY 72/72 PASS · LIVE 71/72 FAIL (REL-06 transport errors; every hard gate held); `/admin/benchmark` shows them | *Hosted E2E smoke P8* |
| H6 budget fail-safe · H7 stale-ledger sweep · H8 copy guard | PASS | `test_config.py`, `test_ledger_sweep_db.py`, `test_evidence_qualification.py` / `test_evidence_pipeline_db.py` |
| H3 crash / restart · H4 backpressure per job type · H5 cache · H15 feedback | PASS | `test_hardening_db.py`, `test_hardening_unit.py`, existing worker / pipeline / verification tests |
| H11 chatgpt-2 fixture matrix · H12 active-course picker | PASS | extension unit 100 (`chatgpt-thread-matrix.test.ts`, `courses.test.ts`), Chromium 4 |
| H14 security sweep | PASS | error redaction at write, raw-HTML guard tests, `npm audit` 0, `pip-audit` 0, `supabase db lint` no schema errors |
| H10 recorded signed-in fixture + `SIGNED_IN=1` live check | NOT DONE | needs the owner signed in; hotfix structure-from-live fixture + hosted `chatgpt-2` captures stand in |
| H13 E2E CI job on the replay provider | NOT DONE | the replay provider exists (H2); the job is next |
| Hosted E2E smoke on the merged code (owner-approved) | **PASS**: inspect 4/4 · read model 4/4 · prepare 1/1 · run 1/1 · verify 9/9 · browser 2/2 · cleanup 3/3 · benchmark 5/5 · admin cleanup 2/2 · affected (after the owner's recompute) 8/8 | *Hosted E2E smoke P8* |
| Hosted remediation of the 2026-09-25 row | **COMPLETE**: the owner's targeted `recompute_skill.py --apply`; `affected` **8/8** (read-only): delegations 2 → 1, debt 30.1433 → 0, VERIFY `e8d6092f…` superseded (NO_ACTION active), activity actor = evidence actor, the recompute created 0 model runs / registry / evidence / attribution / raw rows, the real learner unchanged, the READY session untouched | *Hosted E2E smoke P8* |
| `gemini-3.7-flash` canary | **SKIPPED** by the owner (2026-09-26) | — |

**Real live capture (chatgpt-2, hosted, read-only, ids only).** 13 messages in 2 conversations
since 16:04 UTC, all `adapter_version = chatgpt-2`, extension 0.2.0, every reply paired with its
question. Conversation `c4f799a1…` (the defect turn): 8 messages, 4 turns, outcomes
3 `EVIDENCE_RECORDED` + 1 `NON_LEARNING`. The defect was downstream of capture.

**Known-defect matrix (closure).**

| # | Defect | Result |
| --- | --- | --- |
| K1 | Ledger decays only when recomputed | **Closed**: daily sweep (H7) |
| K2 | Copy guard sees 4 messages | **Closed** for the conversation (50 messages, H8); other conversations stay a limitation |
| K3 | Lexical span grounding | Limitation (safe direction); ABS-10 measures it |
| K4 | Delegation depends on the model's type / reason | Measured: actor 0.92, evidence type 0.85 (targets met); Flash-Lite's false credit of questions (SEG-06 and 3 arguable cases) recorded for `skill-attribution/v3` |
| K5 | Difficulty uses the band | Limitation (V1) |
| K6 | Live P3B/P4 = one STUDENT turn | **Closed**: 11 ATT + 2 DEBT + 3 ABS live cases, every hard gate 0 |
| K7 | Pre-0005 turns have no attribution | Tool ready (P7 RESUME_ATTRIBUTION); hosted run waits for approval |
| K8 | 3.7 / 3.8 free tier = 20/day | Limitation |
| K9 / K10 | turn-analysis on 3.7; 3.7 live validation | Not run: the 3.7 canary was skipped by the owner (2026-09-26); Flash-Lite stays the operational runtime |
| K11 | Flash-Lite graph 24 skills; "binary search" → STOP | Live: "Explain binary search in one sentence." now MAP → binary search (EXPOSURE); "What is a regression?" still STOP (soft miss). The gate's graphs are hand-written (the graph-size probe was not re-run) |
| K12 | One retrieval per unit | Limitation (ADR 0004) |
| K13 | Query vectors cached in memory only | Limitation |
| K14 | Outdated Flash-Lite quota record | Closed in P7 |
| K15 | Real-model quality only spot-checked | **Closed**: the 72-case live gate |
| K16 | Leaked Password Protection disabled | Limitation (owner setting) |
| K17 | Relative lexical score fusion | Measured: top-1 1.00, top-3 1.00 on the fixture graphs; no tuning needed |
| K18 | Reply after 120 s analysed alone | Limitation (no duplicates: pipeline tests) |
| K19 | `active_course_id` has no FK | Limitation (by design) |
| K20 | Extension sends no `active_course_id` | **Closed**: active-course picker (H12) |
| K21 | Candidate review UI; graph regeneration | Closed in P7 + limitation (READY graph regeneration) |
| K22 | Signed-in ChatGPT covered by a synthetic fixture only | Partly: hotfix structure-from-live fixture + real hosted `chatgpt-2` captures; H10 recorder not built |
| K23 | No capture-degraded state | **Closed** by the hotfix (not reimplemented); matrix test |
| K24 | Auto-confirm on for hosted | Limitation |
| K25 | Adjudication never seen live | Still scripted only: MAP-16 was accepted at first pass live (≥ 0.80) |
| K26 | P3A smoke live run deferred | **Closed**: absorbed; the 12 `p3a-smoke` cases passed live |
| K27 | Live delegation turn | **Closed**: DEBT-17 live ("How do I write a for loop…": AI OBSERVATION, no debt); DEBT-18 (three delegations) eligible and actionable |
| N1 | Flash-Lite and embeddings unbudgeted | **Closed** (H6) |
| N2–N4, N6 | Course role; bootstrap stage; rejected candidates; raw admin errors | Closed in P7 (+ model-run errors redacted at write, P8) |
| N5 | No worker without a real key | Replay provider (H2); the E2E job (H13) is not built |
| N7 | Unranked invented mapping crashed the turn | **Closed** (found by the gate) |
| N8 | Activity actor ≠ evidence actor; copy-guard reclassification counted as delegation; learner explanation lost | **Closed** in code (§24–26, merged) and proven live on hosted with a disposable learner (the attribution validator refused the reused-loop span, the repair credited the learner's explanation). The 2026-09-25 row: remediated on hosted (read model corrected; ledger recomputed by the owner's targeted `--apply`, `affected` 8/8); the lost explanation stays lost (immutable); its READY check is a P9 UX cleanup item |
| N9 | KaTeX math captured as MathML tokens | **Closed** (H11 matrix) |

### Previous phase: P6 (merged in PR #6, `4a648ac`)

**P6 — Verification Loop.** Branch `skillmirror-p6-verification-loop` from `main`
`a0e6982` (the P5 merge), merged in PR #6 as `4a648ac`. Its migration is
**`0008_verification.sql`**. Hosted has **0001–0008** (0008 pushed 2026-09-25 with the owner's
explicit approval, "0008 only", and verified read-only).
Decisions: [ADR 0007](decisions/0007-p6-verification-loop.md).
**Status: COMPLETE and MERGED. Local acceptance PASS; hosted migration 0008 PASS; hosted real
acceptance PASS on `gemini-3.5-flash-lite` (1 generation, 0 evaluation, 0 embedding requests) —
the REAL LIVE PROOF case (see *Hosted acceptance P6*). The PR-triggered CI on the PR #6 head
passed.**

```
VERIFY / REVERIFY recommendation -> planner (no model call) -> PLANNED -> 1 VERIFICATION_GENERATION
  -> validator -> READY -> start/resume -> submit (stored, idempotent) -> deterministic grade
  (or rubric evaluation) -> result + VERIFICATION EvidenceEvent + EVALUATED (one transaction)
  -> recompute_ledger -> refresh_recommendations
```

| Gate | State | Evidence |
| --- | --- | --- |
| Migration 0008 applies from a clean reset; 0001–0007 unchanged | PASS (local) | `supabase db reset` 0001–0008; pgTAP compares the applied statements of 0001–0007 with the hosted ones; git blob ids pinned |
| pgTAP 0008 (lifecycle, transitions, RLS, grants, uniqueness, evidence ↔ result, ledger guard, server-only items) | PASS: 79 | `supabase/tests/0008_verification.test.sql`; total 288 |
| Planner only from VERIFY / REVERIFY; budget 2/day; cooldowns; idempotent | PASS | `test_verification_planner.py`, `test_verification_db.py` |
| Generator + validator (skill, band, prerequisites, duplicates, code/sql, answer key, ≤ 3 attempts, cache) | PASS | `test_verification_validator.py`, `test_verification_generator.py`, DB regeneration tests |
| Deterministic graders; rubric evaluator (repair once, untrusted → no evidence, 429/503 deferral) | PASS | `test_verification_grading.py`, DB tests |
| Result → exactly one VERIFICATION EvidenceEvent; never a direct ledger write | PASS | DB tests (ledger unchanged until recompute), pgTAP guard |
| VERIFIED gates; one isolated failure keeps VERIFIED; stale / contradicted → NEEDS_REVERIFICATION | PASS | `test_mastery_verification.py` |
| Debt 0.2 / 0.6 / 1.0; no debt without delegation | PASS | `test_debt_verification.py`, DB + acceptance |
| Replay / crash safety (duplicate start/submit/jobs, crash before/after evidence, backpressure, budget) | PASS | `test_verification_db.py` |
| Security (own rows only, no answer key before grading, clients cannot write, anon nothing) | PASS | pgTAP + DB tests |
| Local acceptance (fake provider) + browser walkthrough | **PASS**: 15/15 + 1 passed | `scripts/acceptance_p6.py`, `e2e/p6-acceptance.spec.ts` |
| Hosted migration 0008 | **PASS**: pushed with the owner's approval; catalog, RLS, grants, dropped P4-era constraint, triggers, functions, indexes, checks, policy key and unchanged rows verified | See *Database* |
| Hosted real acceptance (1 Flash-Lite generation, 0 evaluation, 0 embedding) | **PASS**: 12/12 + browser walkthrough; **REAL LIVE PROOF** (the live pass alone met every VERIFIED gate; no evidence was added after it) | See *Hosted acceptance P6* |
| Real P3B/P4 learner untouched | PASS | Row hash `07a84e5a…` identical before the push, after verify and after cleanup |
| CI + pull request | **PASS**: PR #6, CI green on its head `583aea4`; merged by the owner as `4a648ac` | — |

### Previous phase: P5 (merged in PR #5, `a0e6982`)

**P5 — Complete Student Experience** (dashboard, Skill Map, Skill Detail with
"Why?", enriched Activity, corrections, deterministic recommendations). Branch
`skillmirror-p5-student-experience` from `main` `ab2d73d`, merged in PR #5 as `a0e6982`. Decisions:
[ADR 0006](decisions/0006-p5-student-experience.md).
**Status: COMPLETE and MERGED. MIGRATIONS 0001–0007 ON HOSTED (0007 pushed 2026-09-25 with the
owner's approval, verified read-only). The local acceptance and the hosted acceptance PASSED with 0
generation and 0 embedding requests.**

```
GET /v1/ledger · GET /v1/skills/{id} · GET /v1/activity · GET /v1/recommendations  (read, explain)
POST /v1/feedback: DONT_COUNT / WRONG_SKILL -> one-way evidence exclusion -> ledger recompute
                   -> recommendation refresh (one transaction); EVALUATION -> stored only
```

| Gate | State | Evidence |
| --- | --- | --- |
| Migration 0007 applies from a clean reset | PASS (local) | `supabase db reset`: 0001–0007 applied |
| pgTAP 0007 (RLS, own rows, no cross-learner access, idempotency, recommendation uniqueness/states, one-way exclusion) | PASS: 49 | `supabase/tests/0007_student_experience.test.sql`; total 209 |
| 0001–0006 unchanged | PASS | Git blob ids equal `main` `ab2d73d` (`test_migrations_frozen.py`); hosted list 0001–0006 |
| Every meaningful state explained | PASS (tests + local acceptance) | Explanation codes + gates + evidence timeline; `test_experience_db.py`, acceptance check 4 |
| UNKNOWN is never weak / never 0% | PASS | No-row skill → UNKNOWN, null mean, NO_EVIDENCE; neutral "Not enough activity yet"; UNKNOWN never PRACTICE/PREREQUISITE (engine + DB check) |
| Evidence traces to the captured activity | PASS | Acceptance check 5: 13/13 events → raw messages → activity chips |
| Debt explained as a reliance signal | PASS | Band + factors; NONE → "There is not enough repeated AI delegation here to infer reliance."; score only in internal detail |
| DONT_COUNT: exclude → recompute → refresh | PASS | DEMONSTRATED → DEVELOPING, `ledger_version` +1, NO_ACTION → PRACTICE; provenance counts unchanged |
| WRONG_SKILL: exclude, provenance kept, no invented skill | PASS | 13 mappings / 13 evidence rows unchanged; debt 20 → 16 → not eligible; VERIFY → NO_ACTION |
| EVALUATION never changes evidence | PASS | DB check + test |
| Idempotent feedback | PASS | Replay 200 same id; repeat under a new key → same correction; key reuse → 409 |
| Corrections hold for later evidence | PASS | Deferred job's evidence born excluded (pgTAP + DB test) |
| Recommendations deterministic | PASS | Rebuild from scratch = same queue; refresh with unchanged ledger writes nothing; random-ledger invariants |
| Learner scoping (not RLS alone) | PASS | Every service filters by the JWT learner; foreign skill/target → 404 |
| Zero model calls | PASS | P5 modules never load the ModelGateway (fresh interpreter); `model_runs` unchanged in DB tests and acceptance (347 → 347) |
| Web: dashboard, Skill Map, Skill Detail, Why?, Activity corrections, course selector, loading/empty/error | PASS | vitest 63; local browser walkthrough (`e2e/p5-acceptance.spec.ts`) passed |
| Local P5 acceptance | **PASS** (10/10 API checks + browser walkthrough) | See *Local acceptance P5* |
| Hosted migration 0007 | **PASS**: pushed with the owner's approval; catalog, RLS, grants, constraints, indexes and policy verified | See *Database* |
| Hosted acceptance (existing course graph, disposable learner, no model call) | **PASS**: 12/12 + browser walkthrough; learner deleted through the Auth cascade | See *Hosted acceptance P5* |
| Real P3B/P4 learner untouched | PASS | Row hash `07a84e5a…` identical before, after verify and after cleanup; its P5 view read in a rolled-back transaction (0 rows written) |
| CI + pull request | on the pushed branch head; the PR is opened after CI is green and not merged by the agent | — |

### Previous phase: P3B + P4 (merged in PR #4)

**P3B — Attribution + EvidenceEvents and P4 — Mastery + Evidence Ledger + AI Assistance Debt.**
Decisions: [ADR 0005](decisions/0005-p3b-p4-evidence-mastery-debt.md).
**Status: COMPLETE and MERGED (PR #4, `ab2d73d`). MIGRATIONS 0001–0006 ON HOSTED (0005 + 0006
pushed 2026-09-25 with the owner's approval). The real P3B/P4 acceptance PASSED on
`gemini-3.5-flash-lite` (the hackathon operational runtime): 1 `TURN_ANALYSIS` + 1
`SKILL_ATTRIBUTION` + 1 embedding.**

```
raw turn → P3A mapping (unchanged) → per MAPPED segment ONE SKILL_ATTRIBUTION call
         → deterministic evidence qualification → immutable EvidenceEvents (one transaction)
         → deterministic ledger recompute: mastery (weighted Beta) + AI Assistance Debt
```

- **Invariants enforced in code, policy and database:**
  - UNKNOWN is not weak.
  - AI use is not dependency.
  - Exposure/observation have strength 0.
  - Evidence precedes judgement.
  - Uncertain attribution abstains.
  - A single AI interaction never creates debt.
- **Evidence sources.** `evidence_events` accepts every §10.1 source (`AI_ACTIVITY |
  VERIFICATION | ASSESSMENT | TEACHER`), so P6 can insert VERIFICATION evidence without a
  schema change. The attribution/mapping provenance guard applies to `AI_ACTIVITY` only.
- **VERIFIED / NEEDS_REVERIFICATION are unreachable before P6.** No source counts as
  verification yet, and `skill_ledger` refuses both states.
- **Nothing in P5–P8 was started.**

| Gate | State | Evidence |
| --- | --- | --- |
| Migration 0005 (attribution + evidence) | PASS: pgTAP 40 locally; **on hosted**, verified | `supabase/tests/0005_attribution_evidence.test.sql`; hosted catalog check (below) |
| Migration 0006 (ledger + debt) | PASS: pgTAP 15 locally; **on hosted**, verified | `supabase/tests/0006_mastery_debt.test.sql` |
| 0001–0004 unchanged | PASS | Blob hashes identical to `main` `08260a3`; hosted list 0001–0006 local = remote |
| Attribution only for ACCEPTED mappings | PASS (tests + DB trigger + **live**) | `test_only_accepted_mappings_are_attributed`, pgTAP; live: the one ACCEPTED mapping was attributed |
| Actors STUDENT / AI / SHARED / UNKNOWN | PASS | `test_attribution.py`, `test_evidence_qualification.py`; live: STUDENT |
| Invalid output → one repair → abstain | PASS | `test_invalid_attribution_output_is_repaired_once_then_abstains`; ABSTAINED rows, no evidence, mapping stays |
| Evidence replay never duplicates | PASS (tests + **live**) | Unique keys + advisory lock; live replay: 0 requests, runs/attributions/evidence 3/1/1 → 3/1/1 |
| Provenance complete | PASS (tests + **live**) | Live: evidence → attribution → ACCEPTED mapping → MAPPED decision → MAP segment → 2 raw messages; all 3 model runs exist |
| Evidence immutable | PASS | Update → 55000; only the one-way exclusion (P7 feedback) is allowed |
| EXPOSURE / OBSERVATION strength 0 | PASS | Engine, policy validator and DB check; 24 parametrized cases |
| Low confidence ≠ weak evidence | PASS | Below 0.80: abstain (`LOW_ATTRIBUTION_CONFIDENCE`) |
| Incorrect attempt → negative evidence | PASS | outcome 0.0 with strength > 0 |
| Mastery math, partial/incorrect, recency | PASS | `test_mastery.py` (exact α/β, half-life 180 d) |
| UNKNOWN before thresholds; strong mean + low support = UNKNOWN | PASS | `test_unknown_is_checked_before_every_mean_threshold`, `test_strong_mean_with_insufficient_support_remains_unknown` |
| VERIFIED / NEEDS_REVERIFICATION unreachable | PASS | 40 seeded random evidence sets, incl. VERIFICATION-source rows; ledger check in pgTAP |
| One AI interaction → no debt; repeated → eligible | PASS (tests + DB pipeline) | `test_repeated_delegation_creates_debt_but_a_single_one_does_not`: eligible False, True, True |
| Heavy AI + strong independent → low debt | PASS | Score < 5 (unit), ≤ 8 (benchmark) |
| Recompute idempotent | PASS (tests + **live**) | Same UTC day ⇒ identical row; live: rebuild ×2, `ledger_version` 3 → 3, 0 model runs |
| False-debt safety benchmark | PASS | `p3b-p4-safety` 22/22; False AI Assistance Debt Rate **0/21**; debt recall 2/2 |
| ModelGateway efficiency (ADR 0004) | PASS | 1 attribution request per mapped segment; identical repeat 0 requests; 503 defers with 0 attempts and resumes without repeating P3A; spent budget defers before any request |
| `GET /v1/ledger` | PASS | `test_ledger_api.py`: UNKNOWN + null mean without evidence, own rows only, course filter 404 |
| Local suites + CI | PASS locally; CI on the PR | See *Automated results* |

### Previous phase: P2 + P3A (merged in PR #3)

**P2 — Courses + Skill Graph and P3A — Qualification + Retrieval + Mapping.**
**Status: COMPLETE. MIGRATIONS 0001–0004 ON HOSTED. The provider-independent real end-to-end
acceptance PASSED on 2026-09-25 on `gemini-3.5-flash-lite`. The 3.7-specific live validation is
PENDING, because Google returned repeated HTTP 503 "high demand".**

- Everything that runs without a real model passes locally, with a scripted fake provider.
- **Free-tier execution (ADR 0004)** is implemented and verified live:
  - Local lexical + pgvector retrieval builds the top-20 pool first. One `TURN_ANALYSIS`
    request then returns segmentation, qualification, the top-8 and the mapping proposals.
  - At most one adjudication request per turn.
  - Exact result cache with `model_runs` provenance.
  - Daily request budget with a safety reserve.
  - Opt-in routing.
- **The real chain passed end to end** on hosted Supabase and the real Gemini API: course graph →
  768-d embeddings → lexical + pgvector retrieval → a captured turn through combined
  `TURN_ANALYSIS` → top-20 → top-8 → mapping → provenance back to `raw_messages`. An identical
  repeat was served from the cache with **0 provider requests** (see *Live acceptance*).
- **Model used for the passing run:** `gemini-3.5-flash-lite`, set only as a runtime override
  (`GEMINI_GENERATION_MODEL`) for this acceptance. It is not committed.
- **The configured default remains `gemini-3.7-flash`** in the code, `.env.example`, ADR 0003 and
  `policy_config`. Its live validation is **pending**:
  - After the quota reset, `gemini-3.7-flash` answered 5 of 6 requests with HTTP 503 ("This
    model is currently experiencing high demand"), from 07:20 to 07:56 UTC. The only success was
    the minimal quota check.
  - The owner decided not to spend more 3.7 requests waiting for capacity.
  - The same ModelGateway pipeline has passed against another supported real Gemini model, so
    this provider outage is not a product blocker.
- No billing or paid tier was used. *(Corrected 2026-09-25: this record used to say "20
  generation requests per day and 5 per minute" for every model. That is the limit of
  `gemini-3.7-flash` / `gemini-3.8-flash` only; see *Gemini free-tier limits* below for the
  owner-verified, dated values per model.)*

| Gate | State | Evidence |
| --- | --- | --- |
| P0/P1 remain green | PASS | All P0/P1 tests pass locally; CI jobs unchanged apart from extensions |
| Migration 0003 applied/tested | PASS | pgTAP 49 assertions locally; applied to hosted 2026-09-25 (owner-approved), migration lists match, RLS/grants verified |
| Arbitrary course can be created | PASS (tests + **live on hosted**) | `test_courses_api.py`; live: fresh learner → `POST /v1/courses` → course + membership + bootstrap job, no model call in the request |
| Course skill graph generated | PASS (tests + **live**) | Live (`gemini-3.5-flash-lite`): 1 generation request, attempt 1, no repair → graph v1 with 24 assessable skills, 5 topics, 48 aliases, 66 edges, 0 alias conflicts (inside the 20–80 hard bounds, below the 30–60 target) |
| Canonical registry works | PASS | Immutable UUID, rename → `PREVIOUS_NAME`, merge → `MERGED`, pgTAP + DB tests |
| Aliases / canonicalization | PASS | Variants (e.g. Visualization/Visualisation, plural, case) → one UUID; alias conflicts refused |
| Embeddings stored | PASS (tests + **live**) | Live: 24 × 768-d `gemini-embedding-2` vectors stored in 1 embedding request, each with its content hash and `model_run_id` |
| Lexical + vector retrieval | PASS (tests + **live**) | `test_retrieval_db.py`; live probe over the real graph: pool 20, 9 lexical matches, every candidate scored by pgvector cosine, 0 generation requests |
| Top 20 → top 8 | PASS (tests + **live**) | Live: `retrieval_candidates` = 20, `mapper_candidate_ids` = 8, both mappings inside the top-8 (DB guard) |
| ModelGateway used by all model-dependent engines | PASS | Only `app/model_gateway/gemini.py` imports the SDK; engines call the gateway |
| model_runs logged | PASS (tests + **live**) | Every live request logged with task, model, prompt_version, status, error code and cache provenance; cache hits reference their source run |
| Relevance / intent / skill-bearing | PASS (tests + **live**) | Live acceptance turn: academic / learn / high@1.0 / skill-bearing@1.0 / `CODE_REQUEST` → `MAP`; pending P1 turn: academic / understand / low@0.8 → `STOP` |
| Segmentation | PASS with fake model | Mixed and multi-task turns → segments, only learning ones mapped |
| Mapping confidence gates | PASS | 0.80 accept, 0.65–0.79 adjudication, < 0.65 abstain (boundaries tested) |
| Unknown concepts do not silently become active skills | PASS | `skill_candidates` `PENDING_REVIEW`; skill count unchanged; DB guard on mappings |
| Abstention | PASS | Low confidence, unresolved adjudication, invalid output, missing attachment context |
| Provenance to raw activity | PASS (tests + **live**) | Segment → user/assistant raw ids; decision → pool, model runs, prompt versions, policy; verified on hosted for the live turn and its cached repeat |
| Worker durable / idempotent | PASS | SKIP LOCKED (3 concurrent workers), retry, defer, crash recovery, idempotent rerun |
| Local + hosted acceptance | **PASS (provider-independent, on `gemini-3.5-flash-lite`)**; `gemini-3.7-flash` live: **pending (HTTP 503 high demand)** | See *Live acceptance (2026-09-25, second run)* |
| Free-tier call budget (ADR 0004) | PASS (tests + **live**) | Live: graph 1, normal turn 1, identical repeat **0** provider requests; reserve intact. Tests: `test_turn_analysis.py`, `test_model_gateway_free_tier.py`, `test_pipeline_db.py`, `test_gateway_db.py`: ordinary turn = 1 generation call, ambiguous ≤ 2, identical repeat = 0, retrieval = 0 generation, 429/503 = one request then deferral, spent budget = deferral before any request |
| Migration 0004 (cache provenance) | PASS (pgTAP 10 locally; **applied to hosted**, verified) | `supabase/tests/0004_model_gateway_cache.test.sql`; hosted `migration list` 0001–0004 local = remote |
| Complete local suite | PASS | See *Automated results* |
| CI green | PASS on `2801ed4` / `bb2448f`; the ADR 0004 series is verified in the PR checks | See *CI* |

## Database

- Latest migration: **`0009_teacher_admin_ops.sql`** (ADR 0008), **on hosted since 2026-09-25**:
  - enums `audit_actor_type`, `audit_action`, `benchmark_mode`, `benchmark_verdict`
  - `audit_events` (server-only, append-only; one row per admin / operator mutation with its
    Idempotency-Key) and `benchmark_runs` (server-only, append-only; written by P8)
  - `processing_jobs.manual_retry_count` (0–5) + `last_manual_retry_at`, with a guard
  - `skill_candidates.reviewed_by / reviewed_at / review_note`, the review guard, one REJECTED
    row per name
  - teacher-membership guards on `course_memberships` and `profiles.role`
  - indexes for admin lists, cohorts and the P8 stale-ledger recompute
  - policy key `teacher_view` (`{min_cohort: 3, window_days: 30, top_n: 10}`)
  - no new client grant or policy; pgTAP runs an RLS sweep over every public table
  - *Finding (2026-09-25):* hosted **0002 and 0008 were pushed from CRLF working copies** (the
    CLI stores the file's bytes; the SQL is identical). Migration pins now compare CR-normalized
    statements, and a test requires 0009 to be LF on disk before its push.
- Previous: **`0008_verification.sql`** (ADR 0007), **on hosted since 2026-09-25**:
  - enums `verification_state` (PLANNED, READY, IN_PROGRESS, SUBMITTED, EVALUATED, ABANDONED),
    `verification_assessment_type`, `verification_grader_type` (MCQ_EXACT, NUMERIC_TOLERANCE,
    RUBRIC_AI), `verification_evaluator_type` (DETERMINISTIC, AI_RUBRIC)
  - `verification_sessions`: the lifecycle with its legal transitions (guard triggers), the
    stored submission (response, idempotency key, request hash, `submitted_at`) before any
    grading, one open session per learner and skill, and a unique submission key
  - `verification_items`: **server-only** (no client grant, no policy); the expected answer and
    rubric live here; append-only
  - `verification_results`: written only by the grading completion; immutable
  - `evidence_events_verification_guard`: VERIFICATION evidence needs a real result
    (`source_id = result.id`) of the same learner, skill and course
  - `skill_ledger_verification_guard` replaces the dropped 0006 constraint
    `skill_ledger_no_verification_states_before_p6` (the only change to an earlier object)
  - policy key `verification` (planner, difficulty, generation, grading, reverification)
  - RLS on all three; learners SELECT their own sessions and results; no client writes; the six
    guard functions are not executable by clients.
- Previous: **`0007_student_experience.sql`** (ADR 0006), **on hosted since 2026-09-25**:
  - enums `feedback_action` (WRONG_SKILL, DONT_COUNT, EVALUATION), `feedback_target_type`
    (EVIDENCE_EVENT, SKILL_MAPPING, ACTIVITY_SEGMENT, SKILL, RECOMMENDATION), `feedback_verdict`,
    `recommendation_type` (NO_ACTION, PRACTICE, VERIFY, PREREQUISITE, REVERIFY),
    `recommendation_state` (ACTIVE, SUPERSEDED, COMPLETED, DISMISSED)
  - `feedback`: append-only. The guard resolves skill/mapping/segment from the learner's own
    target and re-checks the recorded exclusions. Idempotency: `(user_id, client_request_id)`
    plus one DONT_COUNT/WRONG_SKILL per target. Evaluations never change evidence.
  - `recommendations`: a rebuildable queue with one ACTIVE row per learner and skill. A changed
    action supersedes the row; resolved rows are immutable; UNKNOWN never gets PRACTICE or
    PREREQUISITE.
  - trigger `evidence_events_apply_corrections`: captured-activity evidence of a corrected
    mapping or task unit is inserted already excluded (the 0005 update guard is untouched)
  - policy key `recommendations` (max 2 active verifications, EMERGING prerequisite gap, debt
    bands 15/25)
  - RLS on both tables; learners SELECT their own rows; no client writes; the trigger functions
    are not executable by clients.
- Previous: **`0006_mastery_debt.sql`** (ADR 0005).
  - **`0005_attribution_evidence.sql`**:
    - enums `evidence_actor`, `evidence_type`, `outcome_signal`, `evidence_source_type`,
      `attribution_status`
    - `attributions`: only ACCEPTED mappings, provenance copied, append-only
    - `evidence_events`: all four sources possible; `AI_ACTIVITY` guarded to its attribution
      chain; zero-strength exposure/observation; replay key `(source_type, source_id,
      skill_id)`; B.2 `grading_confidence`; append-only with a one-way exclusion
    - policy keys `attribution`, `evidence`
  - **`0006_mastery_debt.sql`**:
    - `mastery_state` enum
    - `skill_ledger`, a rebuildable cache: no VERIFIED/NEEDS_REVERIFICATION before P6, and no
      debt without eligibility
    - policy keys `mastery`, `debt`
  - All three tables: RLS on, learners SELECT their own rows, server-only writes.
- `0004_model_gateway_cache.sql` (ADR 0004). Before it, `0003_courses_skill_graph.sql`
  adds 13 tables, all with RLS enabled:
  - P2: `courses`, `course_memberships`, `skill_nodes`, `skill_aliases`, `skill_edges`,
    `course_skills`, `skill_embeddings` (`vector(768)`, HNSW cosine), `model_runs`, `policy_config`
  - P3A: `activity_segments`, `mapping_decisions`, `skill_mappings`, `skill_candidates`
  - Also `processing_jobs.outcome` and a `private.is_course_member()` policy helper.
- Clients (`authenticated`) have SELECT only:
  - their own courses, memberships, course overlays and analysis rows
  - ACTIVE registry nodes, aliases and edges

  `model_runs`, `policy_config`, `skill_embeddings` and `skill_candidates` are server-only.
  `anon` has nothing. All writes go through the backend.
- `policy_config` seeds (§9.3, §9.4, Appendix B):
  - retrieval 0.55 semantic / 0.30 lexical / 0.15 course prior, pool 20, rerank 8
  - mapping 0.80 / 0.65
  - qualification floors 0.60
  - processing unit: 4 context messages, 120 s pairing window
  - skill graph 30–60 skills (hard 20–80), default importance 0.5
- pgTAP: `0001` (13), `0002` (33), `0003` (49), `0004` (10), `0005` (40), `0006` (15), `0007` (49), `0008` (79), `0009` (75) = **363**.
- **`0004_model_gateway_cache.sql` (ADR 0004)** is additive:
  - `model_runs.cache_key` and `model_runs.cache_source_run_id`, both nullable
  - two checks on those columns: a cache key only on `SUCCEEDED` rows; a cache hit is a
    first-attempt success
  - a cache lookup index and a provider-request (budget) index
- **Migration numbering:** `0004` is the free-tier ModelGateway/cache optimisation; P3B is
  `0005` and P4 is `0006`. **P5 (feedback + recommendations) is `0007`; P6 verification is
  `0008`; P7 is `0009_teacher_admin_ops.sql` (local only until the owner approves the push); P8
  needs no migration.**
- Hosted: **`0001`–`0009` applied.**
  - **Migration 0009** was pushed on 2026-09-25 with the owner's explicit approval ("Approved: 0009
    only", with the teacher-overview correction applied and re-tested first), with
    `npx supabase@2.117.0 db push --linked`.
    - Before the push: `migration list --linked` showed only 0009 pending; 0001–0008 unchanged
      (git: only 0009 added since `main` `4a648ac`; blob ids pinned; the CR-normalized applied
      statements on hosted equal the pgTAP pins); the linked ref `lrjoexzlcadfoyciydkn` equals the
      backend `SUPABASE_URL`, `DATABASE_URL` and the web `NEXT_PUBLIC_SUPABASE_URL`; 0009 LF on
      disk; the final dry run listed exactly `0009_teacher_admin_ops.sql`; a READ ONLY snapshot
      (row counts + content hashes of the 26 tables, the real learner's rows, the statements)
      was taken with `scripts/hosted_snapshot.py`. Hosted had 0 skill candidates and only STUDENT
      profiles / memberships.
    - Verified after the push (READ ONLY): `migration list --linked` local = remote for 0001–0009;
      0009's recorded statements are LF and equal to the local ones; the 2 tables and 4 enums (all
      labels); RLS on 28/28 public tables; no client privilege and no policy on the new tables;
      `anon` has no privilege on any public table and `authenticated` writes none; the 6 triggers;
      the 5 new functions not executable by clients; the 5 new columns, 9 new checks (validated)
      and 10 new indexes; `policy_config.teacher_view` as seeded (12 keys); the new tables empty;
      no job has a manual retry.
    - Data: only `policy_config` gained its row. `processing_jobs` and the real learner's rows
      hash differently only because of the two new columns; projected onto the pre-0009 columns
      both are byte-identical to before the push. Security advisors: still only the pre-existing
      Auth WARN.
  - **Migration 0008** was pushed on 2026-09-25 with the owner's explicit approval ("Approved:
    0008 only"), with `npx supabase@2.117.0 db push --linked`.
    - Before the push:
      - the final dry run listed exactly `0008_verification.sql`
      - 0001–0007 had the git blob ids of `main` `a0e6982`
      - the linked ref `lrjoexzlcadfoyciydkn` was unchanged and equal to the backend
        `SUPABASE_URL`, `DATABASE_URL` and the web `NEXT_PUBLIC_SUPABASE_URL`
      - a snapshot (row counts + content hashes) of the existing tables and a hash of the real
        P3B/P4 learner's rows were taken
    - Verified after the push, before any acceptance (read-only, a `READ ONLY` transaction):
      - `migration list --linked`: local = remote for 0001–0008
      - the 3 tables exist; RLS is on for every public table (26)
      - the 4 enums have the contract labels
      - grants: `authenticated` SELECT only on `verification_sessions` and
        `verification_results`; **nothing on `verification_items`**; `anon` nothing
      - policies `verification_sessions_select_own` and `verification_results_select_own`
        (`auth.uid() = learner_id`); no policy on the items
      - `skill_ledger_no_verification_states_before_p6` is gone
      - the 8 triggers exist; the 6 new functions cannot be executed by `anon`/`authenticated`
      - the partial unique `verification_sessions_one_active_key` and
        `verification_sessions_submission_key`, and all 10 P6 checks
      - `policy_config.verification` equals the seeded value; 11 policy keys in total
      - the new tables were empty; every existing table had its row count and content hash from
        before the push (only `policy_config` gained its one new row; the 10 older rows are
        identical); the real learner's hash was unchanged
    - Security advisors: still only the pre-existing Auth WARN.
  - **Migration 0007** was pushed on 2026-09-25 with the owner's explicit approval.
    - Before the push:
      - the final dry run listed only `0007_student_experience.sql`
      - 0001–0006 had the git blob ids of `main` `ab2d73d`
      - the linked ref `lrjoexzlcadfoyciydkn` was unchanged and equal to the backend
        `SUPABASE_URL`, `DATABASE_URL` and the web `NEXT_PUBLIC_SUPABASE_URL`
    - Verified after the push (read-only, a `READ ONLY` transaction):
      - `migration list --linked`: local = remote for 0001–0007
      - `feedback` and `recommendations` exist with RLS on; RLS is on for every public table
      - the 5 enums have the contract labels
      - triggers `feedback_guard`, `feedback_append_only`, `recommendations_guard_update` and
        `evidence_events_apply_corrections` (it fires before `evidence_events_guard`)
      - the 3 new functions cannot be executed by `anon`/`authenticated`
      - grants: `authenticated` SELECT only on both tables; `anon` nothing
      - policies `feedback_select_own` (`auth.uid() = user_id`) and `recommendations_select_own`
        (`auth.uid() = learner_id`)
      - the unique `feedback_request_key (user_id, client_request_id)`, the partial unique
        `feedback_one_correction_key` and `recommendations_one_active_key`, and all 12 P5 checks
      - `policy_config.recommendations` = `{max_active_verify: 2, prerequisite_gap_states:
        [EMERGING], debt_bands: {moderate_min: 15, high_min: 25}}`; 10 policy keys in total
      - the existing evidence (1 row) and ledger (1 row) intact
    - Security advisors: still only the pre-existing Auth WARN.
  - **Migrations 0005 + 0006** were pushed together on 2026-09-25 with the owner's approval.
    - Before the push:
      - the linked ref matched the backend `SUPABASE_URL`, `DATABASE_URL` and the web
        `NEXT_PUBLIC_SUPABASE_URL`
      - the final dry run listed exactly `0005_attribution_evidence.sql` and
        `0006_mastery_debt.sql`
      - 0001–0004 were byte-identical to `main`
    - The first approval request was withdrawn before the push: 0005 then still refused every
      evidence source but `AI_ACTIVITY`. That was fixed in `1352b5b`.
    - Verified after the push (read-only catalog queries):
      - `migration list --linked` shows local = remote for 0001–0006
      - RLS is on for every public table
      - `authenticated` has only SELECT on the 3 new tables; `anon` has nothing
      - the three `*_select_own` policies exist (`auth.uid() = learner_id`)
      - the 3 new functions cannot be executed by `anon`/`authenticated`
      - all 9 policy keys are present, the 4 new ones with the seeded values
      - all new checks exist (`source_key`, `ai_activity_shape`, `other_source_shape`,
        `verification_source`, `teacher_source`, `exposure_guard`, `confidence`,
        `no_verification_states_before_p6`, …), and the removed `source_p3b` check is absent
      - the enums have the contract labels; `grading_confidence` is nullable
    - Security advisors: still only the pre-existing Auth WARN.
  - Migration 0003 was pushed on 2026-09-25 with the owner's approval, after these pre-checks:
    - the linked project ref equals the project in the backend `SUPABASE_URL`, the web
      `NEXT_PUBLIC_SUPABASE_URL` and `DATABASE_URL`
    - the dry run listed only `0003_courses_skill_graph.sql`
    - 0001/0002 are unchanged since `main`
  - Migration 0004 was pushed on 2026-09-25 (07:19 UTC) with the owner's approval, after the
    same pre-checks:
    - the linked ref matches all three env values
    - the dry run listed only `0004_model_gateway_cache.sql`
    - 0001–0003 are byte-identical to `HEAD`; 0001/0002 also to `main`, and 0003 has been
      unchanged since `76483c8`, which was pushed
    - 0004 holds only the columns, checks, comments and indexes above

    Verified after the push:
    - `supabase migration list --linked` shows local = remote for `0001`–`0004`
    - both columns are nullable
    - both checks are validated
    - both indexes exist
    - RLS is still on, and `anon`/`authenticated` have no grants on `model_runs`
  - Read-only catalog check (after 0003):
    - 18/18 public tables have RLS enabled
    - `anon` has SELECT on none, and `authenticated` has no INSERT/UPDATE/DELETE
    - `model_runs`, `policy_config`, `skill_candidates`, `skill_embeddings` are server-only
    - the policy seed is present (weights 0.55/0.30/0.15)

  Security advisors: one WARN, *Leaked Password Protection Disabled*. This is an Auth project
  setting, not from the migrations.

## Gemini free-tier limits (owner-verified in Google AI Studio on 2026-09-25)

These are **dated operational limits** of this project's free tier, not architecture assumptions:
Google can change them, so recheck before a live run. Billing / paid tier stays **off**.

| Model | RPM | TPM | RPD | Role |
| --- | --- | --- | --- | --- |
| `gemini-3.5-flash-lite` | 15 | 250K | **500** | hackathon operational runtime (environment override only) |
| `gemini-3.7-flash` | 5 | 250K | 20 | architecture / committed default |
| `gemini-3.8-flash` | 5 | 250K | 20 | second fallback |
| `gemini-embedding-2` | 100 | 30K | 1000 | embeddings |

- The architecture / default generation model remains **`gemini-3.7-flash`** (code,
  `.env.example`, ADRs). The code's default budget (`MODEL_DAILY_REQUEST_LIMITS=
  gemini-3.7-flash=20,gemini-3.8-flash=20`, reserve 2) matches that default.
- The hackathon runtime override (never committed): `GEMINI_GENERATION_MODEL=gemini-3.5-flash-lite`
  and/or `GEMINI_ROUTINE_MODEL=gemini-3.5-flash-lite`, `GEMINI_GENERATION_RPM=12`,
  `MODEL_DAILY_REQUEST_LIMITS=gemini-3.5-flash-lite=500,gemini-3.7-flash=20,gemini-3.8-flash=20`,
  `MODEL_QUOTA_RESERVE=25`.
- Quotas are per project and model and reset at midnight Pacific. Every provider request counts
  toward the local budget, including 429/503 responses.

## Intelligence configuration

| Item | Value |
| --- | --- |
| Generation model (architecture default) | `gemini-3.7-flash`: the committed default (code, `.env.example`, ADR 0003/0005). Its live validation is pending (503 on 2026-09-25) |
| Generation model (hackathon operational runtime) | `gemini-3.5-flash-lite`, set in the environment only, never committed (ADR 0005 §26). AI Studio free tier, read by the owner 2026-09-25: Flash-Lite 15 RPM / 250K TPM / **500 RPD**; 3.7-flash and 3.8-flash 5 RPM / 20 RPD; `gemini-embedding-2` 100 RPM / 1000 RPD. Runtime env: `GEMINI_GENERATION_MODEL=gemini-3.5-flash-lite` (or `GEMINI_ROUTINE_MODEL`), `GEMINI_GENERATION_RPM=12`, `MODEL_DAILY_REQUEST_LIMITS=gemini-3.5-flash-lite=500,gemini-3.7-flash=20,gemini-3.8-flash=20`, `MODEL_QUOTA_RESERVE=25` |
| Embedding model | `gemini-embedding-2`, 768 dimensions (task via text prefix; one `Content` per text) |
| SDK | `google-genai` 2.25 (only in `app/model_gateway/gemini.py`) |
| Prompt versions | `skill-graph-bootstrap/v1`, `turn-analysis/v1` + `turn-adjudication/v1` (combined, default), `relevance-intent/v1`, `skill-rerank/v1`, `skill-mapping/v1`, `mapping-adjudication/v1` (staged), **`skill-attribution/v1`** (P3B, routine task) |
| Embedding input versions | `skill-embedding-text/v1`, `retrieval-query/v1` |
| Analysis / mapper version | `p3a-v1` (both modes) / `mapper/p3a-turn-v1` (combined), `mapper/p3a-v1` (staged) |
| P3B / P4 versions | attribution `p3b-v1` (idempotency key), `attributor/p3b-v1`, qualifier `evidence/p3b-v1`, ledger `ledger/p4-v1`; **P8:** `skill-attribution/v2` + `attributor/p8-v1`, qualifier `evidence/p8-v1`, ledger `ledger/p8-v1` (ADR 0008 §24–26) |
| Deterministic stages (no model call) | evidence strength = base × (0.75 + 0.5 d) × independence × min(mapping, attribution); mastery weighted Beta(1,1), recency half-life 180 d in whole UTC days, UNKNOWN below support 1.0; debt eligible at ≥ 2 recent (30 d) accepted high-confidence AI/SHARED delegations; `100 × pressure × gap × importance × confidence × factor`, the verification factor 0.6 unverified (the only value before P6) / **0.2 recently passed / 1.0 failed** (P6); actionable ≥ 15 |
| Turn execution | `TURN_ANALYSIS_MODE=combined` (default): retrieval → 1 `TURN_ANALYSIS` call → gate → ≤ 1 adjudication; `staged` selectable |
| Routing policy | `architecture-default` (everything on `GEMINI_GENERATION_MODEL`); `free-tier` when `GEMINI_ROUTINE_MODEL` is set (routine tasks on it - the turn tasks, attribution and, since P6, `VERIFICATION_GENERATION` / `VERIFICATION_EVALUATION`; graph and adjudication on the default model) |
| Request budget | code default `MODEL_DAILY_REQUEST_LIMITS=gemini-3.7-flash=20,gemini-3.8-flash=20`, `MODEL_QUOTA_RESERVE=2` (the architecture-default models); hackathon runtime adds `gemini-3.5-flash-lite=500` with reserve 25 (see *Gemini free-tier limits*); quota day midnight Pacific; deferral outcome `MODEL_BUDGET_RESERVE` |
| Result cache | exact key (provider, model, task, prompt version, input hash); memory + durable (`model_runs`) for structured output, memory for query vectors; hits logged with `cache_source_run_id` |
| Job types | `BOOTSTRAP_COURSE_GRAPH`, `PROCESS_RAW_MESSAGE`, **P6:** `GENERATE_VERIFICATION`, `GRADE_VERIFICATION` (entity: the verification session) |
| P6 versions | prompts `verification-generation/v1`, `verification-evaluation/v1` (both ROUTINE tasks); planner `verification-planner/p6-v1`, generator `verification-generator/p6-v1`, validator `verification-validator/p6-v1`, graders `grader/mcq-exact-v1`, `grader/numeric-tolerance-v1`, `grader/short-exact-v1`, evaluator `evaluator/rubric-ai-v1`, evidence `verification/p6-v1`; ledger **`ledger/p6-v1`**, recommendations **`recommendations/p6-v1`** |
| P5 versions (no model call) | recommendations `recommendations/p5-v1` (Engine 16: REVERIFY → VERIFY (actionable debt, max 2 active) → PREREQUISITE (EMERGING prerequisite) → PRACTICE → NO_ACTION); explanation codes + gates; debt bands NONE/LOW/MODERATE/HIGH at 15/25 |

## Hosted E2E smoke P8 (2026-09-25 19:29–20:10 UTC): PASS on the merged code; hosted remediation COMPLETE

Owner approval (2026-09-26): the hosted E2E smoke and the benchmark recording; the 3.7 canary
skipped; no migration pushed. Code: `main` `01e828c`. Local FastAPI :8001 (from
`services/backend/.env`, blank `GEMINI_API_KEY`, `WORKER_ENABLED=false`: no gateway, no worker) and
Next.js :3001 → hosted Supabase (0001–0009). Script: `services/backend/scripts/smoke_p8_hosted.py`
(phases below) + `apps/web/e2e/p8-smoke.spec.ts`. Evidence: `test-results/p8-hosted/`
(git-ignored; ids only; credential files deleted at cleanup).

**The 2026-09-25 learner / skill** (learner `8afbd2c8…`, "Writing for loops over ranges"
`2761328b…`). Before the recompute (read-only):

| # | Check | Result |
| --- | --- | --- |
| I3 | stored row vs the merged code | stored `ledger/p6-v1`: debt eligible, **30.14**, **2** delegations; the merged code (`ledger/p8-v1`) derives: not eligible, **0**, **1** delegation. Evidence `34e30938…` (AI OBSERVATION, QUALIFIED) counts; `91b0d433…` (COPIED_FROM_AI) does not |
| M1 | activity actor == evidence actor (merged read model, 5 chips) | **PASS**: 0 mismatches; the defect chip (mapping `aeac8331…`) now shows actor AI, attributed actor STUDENT, OBSERVATION, COPIED_FROM_AI |
| M2 | nothing of the learner changed | PASS (evidence, attributions, raw rows, ledger, recommendations: same hashes) |
| M3 | READY verification session `3aa4481e…` | **untouched, still READY** (recommendation `e8d6092f…`). No rule changes a READY session (the planner abandons only stale IN_PROGRESS ones); only the learner can start or abandon it |
| M4 | the stored row | still the old false debt (the recompute had not run yet) |

- **The recompute.** `scripts/recompute_skill.py --learner 8afbd2c8-… --skill 2761328b-…`: the
  agent's dry run (read-only) printed the expected change (2 → 1, 30.1 → 0, `ledger/p8-v1`). The
  agent's `--apply` was refused by its permission policy (a write to a real learner's rows), so
  **the owner ran `--apply`** after the smoke (the recomputed row's `computed_as_of`:
  2026-09-25 20:06:05 UTC). It was run once; it is not to be repeated.
- **After the recompute** (`smoke_p8_hosted.py affected --baseline snapshot-after.json`,
  read-only): **PASS 8/8**.

| # | Check | Result |
| --- | --- | --- |
| A1 | the row | `ledger/p8-v1`: **delegations 2 → 1, debt 30.1433 → 0**, not eligible; mastery UNKNOWN (unchanged) |
| A2 | idempotent | the copy-guard record (`91b0d433…`) is not a delegation; a new recompute would change nothing |
| A3 | no false VERIFY | `e8d6092f…` SUPERSEDED; the skill's active action is NO_ACTION / NOT_ENOUGH_EVIDENCE (`9ef4427e…`); no new VERIFY |
| A4 | the learner's other 47 active recommendations | unchanged |
| A5 | activity actor == evidence actor | 5 chips, 0 mismatches; the defect chip: actor AI, attributed STUDENT, OBSERVATION, COPIED_FROM_AI |
| A6 | the learner's rows | only `skill_ledger` + `recommendations` changed; raw messages, segments, mappings, attributions, evidence identical |
| A7 | READY session `3aa4481e…` | untouched, still READY (its recommendation is now SUPERSEDED) |
| A8 | what the recompute itself wrote (vs the whole-database snapshot taken after the smoke, 19:55:59 UTC, before the recompute) | only `skill_ledger` and `recommendations` changed; `model_runs` 83, `skill_nodes` 67, `skill_aliases` 156, `skill_edges` 178, `course_skills` 80, `evidence_events` 6, `attributions` 6, `raw_messages` 24: identical rows and hashes, **0 rows created after the baseline** (the newest model run is the smoke's, 19:49:01 UTC); the real P3B/P4 learner unchanged against that post-smoke baseline; migrations unchanged |

- **A8 first failed on a stale baseline, not on data.** Its first version compared the absolute
  model-run count with the one `inspect` recorded **before** the smoke (76). The smoke then
  added its own 7 model runs (83); the registry and the real learner matched. The check now
  compares against a whole-database snapshot taken right before the recompute (new read-only
  `baseline` phase; this time the post-smoke snapshot, which predates the recompute). It also
  requires 0 rows created after that snapshot in the eight tables above. No data was changed to
  make it pass.
- **Why not the startup sweep.** A worker of the merged code sweeps at startup. A rolled-back
  preview on hosted (before the recompute) showed that sweep would rewrite 5 rows: the 4 rows of
  `8afbd2c8…` (the affected one as above; 3 only change their algorithm tag) and the one row of
  **the real P3B/P4 learner `97d8e181…`** (`ledger/p4-v1` → `p8-v1`, values unchanged). It would
  also insert **24 NO_ACTION recommendations** for that learner, who has none yet. The owner's
  rule keeps that learner untouched, so the smoke worker was registered for PROCESS_RAW_MESSAGE
  only, with no sweep. The next start of the demo runtime on `main` (`npm run demo`) will
  re-tag the 3 remaining `8afbd2c8…` rows and the real learner's row, and insert those 24
  NO_ACTION rows. The remediated row's values stay; only its `computed_as_of` advances.
- The lost learner explanation was not recreated (immutable evidence; the segment is attributed).
- **P9 UX cleanup item:** the READY check `3aa4481e…` stays in that learner's verification centre
  although its VERIFY is superseded. P9 decides how the UI presents (or lets the learner dismiss)
  a READY check whose reason is gone. The data stays as is until then.

**Disposable part** (one `@mailinator.com` learner `39b2eaab…`, STUDENT of course `9440004a` only,
no registry row; two ChatGPT turns of the defect's shape, synthetic text, sent to
`POST /v1/events/batch` as the extension's `chatgpt-2` envelope with `active_course_id`):
turn 1 "How do I loop over a list…" → the AI's loop; turn 2 the learner reuses that loop plus their
own explanation and asks the AI to check it. Worker: the production `Worker` +
`process_raw_message_job`, real gateway, Flash-Lite runtime overrides (not committed).

| # | Check (hosted) | Result |
| --- | --- | --- |
| I1–I2 | migrations; open jobs | exactly 0001–0009; 0 open jobs (so the worker could only run ours) |
| R1 | capture → processing | 4 messages accepted, 4 jobs COMPLETED at attempt 1 (2 EVIDENCE_RECORDED, 2 DEFERRED_TO_ASSISTANT) |
| V1 | requests | **5 generation on `gemini-3.5-flash-lite`** (2 TURN_ANALYSIS + 3 SKILL_ATTRIBUTION) + 2 EMBED_QUERY; 7 of the 7 model runs in the window are the smoke's |
| V2 | turn 1 | "Writing for loops over sequences" + "Iterating over lists": AI / EXPOSURE, strength 0 |
| V3 | turn 2 (§26 live) | the first attribution quoted the reused loop as the learner's span: **refused by the attribution validator** (INVALID_OUTPUT, "quotes text the assistant already wrote earlier"); the one repair gave the loop to the AI (OBSERVATION) and credited **the learner's own explanation: STUDENT / INDEPENDENT_EXPLANATION, strength 0.4725**; no reused text credited |
| V4 | `GET /v1/activity` | every chip's actor, type and reason = the recorded evidence (4/4) |
| V5 | ledger | = the merged code's derivation; delegations 1 and 0; no debt; no COPIED_FROM_AI record counted |
| V6 | recommendations, planner | no VERIFY / REVERIFY; `GET /v1/verifications` plans nothing; 0 model runs from the GETs |
| V7 | `GET /v1/skills/{id}` | timeline = recorded evidence (4 items) |
| V8 | replay of the 4 jobs | ALREADY_ANALYZED / DEFERRED_TO_ASSISTANT: 0 provider requests, 0 model runs, 0 new rows |
| V9 | untouched | real P3B/P4 learner, `8afbd2c8…` and the registry (67 nodes, 178 edges, 156 aliases, 29 course skills) |
| browser | `p8-smoke.spec.ts` learner | activity chips ("Actor: AI · The AI did it", "Actor: You · Explained it yourself"), both skill pages without VERIFY, `/verifications` empty; 1 passed |
| C1–C3 | cleanup | Auth admin delete 200; 0 learner-owned rows, 0 memberships, 0 profiles, 0 auth users; the 7 model runs stay as the quota record with the learner id nulled (`on delete set null`); shared rows unchanged |

"Iterating over lists" (`95e76e22…`) is a skill of the demo course `de89f3c1…`, not of `9440004a`:
retrieval's stage 2 searches the global registry by design, so a mapped skill can be outside the
learner's course. `GET /v1/ledger` lists course skills only; the smoke read that row from
`skill_ledger`.

**Benchmark recording.** The three saved runner reports (copied unchanged to
`test-results/p8-hosted/reports/`, sha256 below) were inserted with
`critical_gate.py record --from-report <report> --record-to <hosted>`, one each, count checked
before each insert (append-only):

| Mode | `benchmark_runs` id | Result | Verdict | Requests | Model | Code | Report sha256 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| DETERMINISTIC | `82341ff1…` | 120/120 | PASS | 0 | scripted | `fe15731` | `78831ffbbe4f5251…` (`det_final.json`) |
| REPLAY | `edaf9cbd…` | 72/72 | PASS | 0 | gemini-3.5-flash-lite | `fe15731` | `ae0ed91aadb5e7fb…` (`replay2.json`) |
| LIVE | `49359994…` | 71/72 (REL-06 failed) | FAIL | 183 (115 generation + 68 embedding) | gemini-3.5-flash-lite | `fe15731` | `90e600d603abbdef…` (`live_full.json`) |

- B2: each row equals its report field by field (counts, hard gates, metrics, requests, model,
  prompt versions, policy hash, code SHA, verdict, times): empty diff. All 13 hard gates held in
  all three.
- B3 + browser: a disposable ADMIN (signup, `grant_role.change_role`, one audited ROLE_CHANGE) read
  `GET /v1/admin/benchmark` (3 runs, latest per mode = these ids) and `/admin/benchmark` showed the
  three cards (PASS 120/120, PASS 72/72, FAIL 71/72 (1 failed), "All hard gates held.") and 3 table
  rows; 1 passed. The admin was deleted (Auth cascade); its ROLE_CHANGE row stays (actor null).
- The local-only fresh re-record (5/5) was not inserted on hosted.

**Whole-database check** (`hosted_snapshot.py --compare`, before vs after): only `benchmark_runs`
(+3), `model_runs` (+7, the smoke's) and `audit_events` (+1 ROLE_CHANGE) changed. Every other table
is identical: ledger, recommendations, verification sessions, evidence and the rest. The real
learner and the earlier migrations are unchanged.

## Hosted acceptance P7 (2026-09-25): PASS, no model call

Local FastAPI :8001 → hosted Supabase (0001–0009), started from `services/backend/.env` with a
blank `GEMINI_API_KEY` and `WORKER_ENABLED=false` (no gateway, no worker); local Next.js :3001 →
hosted Supabase Auth + that backend. Script: `services/backend/scripts/acceptance_p7.py` (prepare,
the browser walkthrough `apps/web/e2e/p7-acceptance.spec.ts`, verify, cleanup), rehearsed locally
first. Evidence: `test-results/p7-hosted/` (git-ignored; ids only; cleanup deleted the credentials).

- **No shared rows.** The class is the existing course `9440004a-a25e-4e15-94c0-17c21f6bd695`
  (24 assessable skills; 1 existing STUDENT member, the real learner). The 3 fixture students
  joined it with learner-owned STUDENT memberships and P5 evidence on 5 of its canonical skills
  (production code, no model call); the teacher joined through the admin enrollment endpoint. The
  small group was a disposable course **with no skills**. No skill_nodes, aliases, edges,
  course_skills or embeddings were created (counts equal before, during and after).
- **Six disposable accounts** (hosted Auth signup, `@mailinator.com`): 3 students, a teacher, an
  outsider teacher, an admin; roles granted by the operator path (`grant_role.change_role`, 3
  audited ROLE_CHANGE events). All deleted through the Auth admin API at the end.
- **Two synthetic candidates** named `ACCEPTANCE TEST P7 synthetic candidate …` (no course, no
  parent), REJECTED only (one in the browser, one over the API) and deleted at cleanup; 0 remain.
- **The class baseline** (the real learner alone: all 24 skills UNKNOWN, 1 own-work evidence) was
  read before the fixture joined (READ ONLY) and re-read right before the check with the fixture
  memberships removed inside a rolled-back transaction; it had not changed.
- **Concurrent activity.** Another checkout (`lappu1-demo-hotfix`, the live demo) was attached to
  hosted with a worker: it completed the browser-retried fixture job (a re-run of an analysed,
  attributed turn: 0 model runs), and its own work added model runs during the window. None of
  them belongs to the acceptance's accounts, jobs or course.

| # | Check (hosted) | Result |
| --- | --- | --- |
| V1 | `GET /v1/me` roles from the database | 3 STUDENT, 2 TEACHER, 1 ADMIN |
| V2 | `user_metadata {role: ADMIN}` set by the client itself (`PUT /auth/v1/user`) | still STUDENT; admin + teacher routes 403 |
| V3 | anonymous | 401 on all 17 P7 routes |
| V4 | matrix (16 routes) | student 403 everywhere; teacher 200 on its class, 403 on admin; outsider teacher 404 on the class overview; **admin 404 on the teacher overview** (not a member) and 200 on the admin course lookup |
| V5 | teacher list | the class 4 students (not suppressed), the small group 2 (suppressed) |
| V6 | overview = baseline + fixture, exactly | per-skill states equal for all 24 skills; totals UNKNOWN 87 · EMERGING 3 · DEVELOPING 3 · DEMONSTRATED 3 (96 = 24 × 4); own-work evidence 28 (1 + 27), 4 students with evidence; verification need: comprehensions 3 students; 4 skills worked on by 3 students |
| V7 | privacy | no fixture or real learner id, no e-mail, no debt / actor / AI-usage field |
| V8 | suppression | 2 students → cohort size only |
| V9 | N2 | the teacher's `/v1/courses` and `/v1/ledger` empty; skill detail and recommendations of the class 404 |
| V10 | admin reads | failed job error redacted (`[redacted-api-key]`, `[redacted-email]`); model runs without `output`; lookup (4 students, 1 teacher); the candidate queue |
| V11 | retry over the API | FAILED → PENDING (count 1); replay 200 `replayed`; same key + other body 409; again 409 `NOT_FAILED`; one JOB_RETRY audit row (ADMIN) |
| V12 | the browser's retry and reject | retried once (then completed by the demo worker at 0 model runs); candidate REJECTED; both audited |
| V13 | REJECT over the API | 200, no skill, no embedding job; replay `replayed`; a second review 409 |
| V14 | enrollment | replay 200; a STUDENT profile as TEACHER 422 |
| V15 | audit trail | 3 ROLE_CHANGE, 4 COURSE_MEMBER_ADD, 2 JOB_RETRY, 2 CANDIDATE_REJECT |
| V16 | zero model calls; real learner | 0 model runs belong to the run; the real learner's rows unchanged |
| V17 | shared rows | skill_nodes / edges / aliases / course_skills / embeddings unchanged |

**Browser walkthrough (hosted):** 3 passed (teacher overview + suppressed small group; a student's
forbidden panels; admin overview, retry, reject, model runs, benchmark). 10 screenshots.

**Cleanup:** 6 accounts deleted through the Auth cascade (0 learner-owned rows, 0 memberships, 0
profiles, 0 auth users); 0 ACCEPTANCE TEST candidates; the small course gone; the class has exactly
its original member; shared rows unchanged; 0 model runs belonged to the run; the real learner
unchanged. The 11 audit events stay as the record of the acceptance's admin actions (actor ids now
null).

## Hosted acceptance P6 (2026-09-25): PASS on `gemini-3.5-flash-lite`, REAL LIVE PROOF

Local FastAPI :8001 → hosted Supabase (0001–0008), started from `services/backend/.env` with
`GEMINI_API_KEY=` (empty) and `WORKER_ENABLED=false`: the API itself had no ModelGateway, so
planning, start, submit and reads made no provider request. The challenge was generated by a
separate **P6-only worker** (`acceptance_p6_hosted.py worker`, registered only for
`GENERATE_VERIFICATION` / `GRADE_VERIFICATION`) on the real Gemini gateway with the hackathon
runtime overrides in its process environment only (`GEMINI_GENERATION_MODEL` /
`GEMINI_ROUTINE_MODEL=gemini-3.5-flash-lite`, `GEMINI_GENERATION_RPM=12`,
`MODEL_DAILY_REQUEST_LIMITS=gemini-3.5-flash-lite=500,…`, `MODEL_QUOTA_RESERVE=25`; nothing
committed). Local Next.js :3001 → hosted Supabase Auth + that backend. Script:
`services/backend/scripts/acceptance_p6_hosted.py` (`inspect`, `prepare`, `worker`, `ready`, the
browser walkthrough `e2e/p6-acceptance.spec.ts`, `verify`, `cleanup`). Evidence:
`test-results/p6-hosted/` (git-ignored; ids only; the cleanup deleted the credentials).

- **No new global rows.** The fixture reused the existing canonical skill *Handling exceptions
  with try-except* (band 3, importance 0.8) of course `9440004a-a25e-4e15-94c0-17c21f6bd695`.
  Registry and course overlay (nodes / edges / aliases / course skills) stayed 29 / 66 / 77 / 29.
- **One disposable learner**, signed up through hosted Auth (real JWT), seeded with the
  deterministic P6 fixture through the production qualification and ledger code (no model call):
  3 independent applications at confidence 0.85 and 12 recent AI delegations.
- **Before the verification** the ledger was DEVELOPING: mean **0.780** (< 0.80), support **2.55**
  (< 4.0), no verification pass — every VERIFIED gate unmet. Debt 15.307 (MODERATE, actionable)
  → P5 VERIFY recommendation.
- **The case is the REAL LIVE PROOF**: real Gemini challenge → real validation → real submission →
  deterministic grading → real VERIFICATION EvidenceEvent → ledger / debt / recommendation
  recompute. The single live pass met all three VERIFIED gates by itself. The fixture design was
  `planned` (the pre-pass evidence was sized so that one pass of a band-level item could
  complete the gates); the fallback `complete_after_pass` — deterministic independent evidence
  added *after* the pass — was **not used**, and no evidence was added after the live pass.

| # | Check (hosted) | Result |
| --- | --- | --- |
| 1 | VERIFY recommendation from repeated unverified delegation | DEVELOPING, debt 15.307 MODERATE, VERIFY ACTIVE |
| 2 | Planner creates exactly one session, no model call | 1 PLANNED session; budget day 2026-09-25 (UTC), 1 of 2 planned |
| 3 | Real challenge generated and validated → READY | `gemini-3.5-flash-lite`, `verification-generation/v1`, **attempt 1** (no regeneration), MCQ, difficulty 0.50 in the band [0.35, 0.65], all 8 validator checks true; 1 request, 1115 tokens, 2.9 s |
| 4 | Deterministically gradeable (no evaluation call needed) | MCQ |
| 5 | Submission persisted, deterministic grade | `{"selected": ["A"]}` stored, then `grader/mcq-exact-v1` → PASSED, score 1.0 |
| 6 | Submit idempotency | same key + same answer 200 (replay); same key + different answer 409 |
| 7 | VERIFICATION EvidenceEvent, complete provenance | one event (`2186948a…`), strength 1.5; chain evidence → result → item → session → recommendation / course / skill; 1 model run |
| 8 | Ledger recomputed → **VERIFIED**, every gate met | DEVELOPING → VERIFIED; mean 0.835, support 4.05, recent pass; `ledger_version` 1 → 2; explanation `RECENT_VERIFICATION` |
| 9 | Debt verification factor | 15.307 → 2.504: factor 0.6 (UNVERIFIED) → 0.2 (RECENTLY_PASSED), and the evidence gap narrowed; still eligible (the delegations are unchanged), no longer actionable |
| 10 | Recommendation refreshed | VERIFY COMPLETED → NO_ACTION `RECENTLY_VERIFIED` |
| 11 | Job replay | `GENERATE_VERIFICATION` → `ALREADY_ISSUED`, `GRADE_VERIFICATION` → `ALREADY_EVALUATED`; 0 new rows, 0 requests |
| 12 | Live budget | **1 VERIFICATION_GENERATION, 0 VERIFICATION_EVALUATION, 0 embedding** (Flash-Lite 5 → 6 of 500 RPD that day) |

**Browser walkthrough (hosted):** 1 passed (27.2 s): dashboard VERIFY → Skill Detail (DEVELOPING)
→ Verification Center (preparing → ready) → start → challenge → reload resumes the draft → submit →
result with feedback → Skill Detail VERIFIED → dashboard → center (completed). 11 screenshots.

**Cleanup:** the disposable learner was deleted through the Auth admin API (200); the cascade left
0 rows in every learner-owned table (conversations, raw messages, jobs, segments, decisions,
mappings, attributions, evidence, ledger, recommendations, the three verification tables,
memberships, profile). The one `model_runs` row stays for budget accounting with `learner_id`
null (the P2 FK rule). The real P3B/P4 learner (`97d8e181…`) row hash `07a84e5a…` was identical
before the push, after verify and after cleanup. After cleanup every pre-push table had its
original count and content except `policy_config` (+1, the `verification` key) and `model_runs`
(+1, this generation); 0 open jobs; 0 idle-in-transaction sessions.

**Incidents during the run (both fixed in the script, no product code changed):**
- The first `prepare` lost its hosted pooler connection mid-seed ("server closed the connection
  unexpectedly"). It left a partly seeded disposable learner and an orphaned idle-in-transaction
  backend (the fixture's own `activity_segments` insert) that blocked the Auth admin delete. After
  checking that backend's query, it was terminated; the partial learner was then deleted through
  the Auth cascade (0 rows left, real learner hash unchanged, no model call). The seeding now uses
  one connection and one transaction per turn (`ff10814`), and the rerun passed.
- The first `verify` got 401 *token is not yet valid (iat)*: this machine's clock trailed Supabase
  Auth by about a second and the backend checks `iat` without leeway (pre-existing P0 behaviour,
  see *Known defects*). The script now waits 3 s after sign-in; the backend auth is unchanged.

## Local acceptance P6 (2026-09-25): PASS, scripted fake provider

Full local stack: local Supabase (0001–0008) → local FastAPI :8001 without `GEMINI_API_KEY` + an
in-process worker on the scripted fake provider → local Next.js :3001. Script:
`services/backend/scripts/acceptance_p6.py`; browser walkthrough `apps/web/e2e/p6-acceptance.spec.ts`.
Evidence: `test-results/p6-acceptance/evidence.json` + 11 screenshots (git-ignored).

| # | Check (local) | Result |
| --- | --- | --- |
| 1 | VERIFY recommendation from actionable debt | DEVELOPING, debt 21.69 |
| 2 | Planner: exactly one PLANNED session, no model call | `model_runs` 216 → 216 |
| 3 | Challenge generated, validated → READY | attempt 1, all checks true |
| 4 | Start → IN_PROGRESS; start again resumes; no answer key exposed | PASS |
| 5 | Submission persisted idempotently before grading | 202 / 200 replay / 409; stored SUBMITTED |
| 6 | Deterministic MCQ grade → EVALUATED | PASSED 1.0, 0 evaluation requests |
| 7 | VERIFICATION EvidenceEvent with complete provenance | strength 1.8 |
| 8 | Ledger → VERIFIED | mean 0.838, support 4.175, `ledger_version` 1 → 2 |
| 9 | Debt factor | 21.69 → 3.01 (0.2) |
| 10 | Recommendation refresh | VERIFY COMPLETED → NO_ACTION `RECENTLY_VERIFIED` |
| 11 | Replay | 0 new rows, 0 model requests |
| 12 | Abandoned session | no result, no evidence, no mastery penalty |
| 13 | Failed verification | negative evidence (mean 0.771 → 0.547), factor 1.0, delegation count unchanged |
| 14 | Failure without delegation | no debt fabricated (not eligible, 0.0) |
| 15 | Zero real model calls | all 4 `model_runs` from the fake provider |

Browser walkthrough: 1 passed.

## Hosted acceptance P5 (2026-09-25): PASS, no model call

Local FastAPI :8001 → hosted Supabase (0001–0007), started from `services/backend/.env` with
`GEMINI_API_KEY=` (empty) and `WORKER_ENABLED=false`, so no ModelGateway or worker existed.
Local Next.js :3001 → hosted Supabase Auth + that backend. Script:
`services/backend/scripts/acceptance_p5_hosted.py` (`prepare`, the browser walkthrough,
`verify`, `cleanup`). Evidence: `test-results/p5-hosted/` (git-ignored; ids only; the cleanup
deleted the credentials file).

- **No new global rows.** The deterministic fixture reused five existing canonical skills of
  course `9440004a-a25e-4e15-94c0-17c21f6bd695`, whose real graph has exactly the edges the
  fixture needs:
  - DEMONSTRATED: *Writing for loops over sequences*
  - UNKNOWN, no ledger row: *Writing while loops*
  - debt-eligible: *Using list comprehensions*
  - EMERGING prerequisite: *Assigning variables and data types* → prerequisite of → DEVELOPING
    *Creating and indexing lists*

  Registry and course overlay (nodes / edges / aliases / course skills) stayed 29 / 66 / 77 / 29
  throughout.
- **One disposable learner**, signed up through hosted Auth (real JWT). Only learner-owned rows
  were written: a STUDENT membership, 13 turns (26 raw messages, segments, decisions, mappings,
  attributions, evidence), jobs, ledger, feedback, recommendations. It was deleted at the end
  through the Auth admin API; the cascade left 0 rows in every learner-owned table, 0
  memberships, 0 profile.
- **The real P3B/P4 learner** (`97d8e181…`) was only read.
  - Its P5 view ran inside a rolled-back transaction: 24 skills, the loop skill UNKNOWN
    (`NOT_ENOUGH_SUPPORT`, support 0.7875), 1 evidence event traced to its 2 raw messages,
    recommendation NO_ACTION, 0 rows written.
  - A hash of all its rows was identical before, after verify and after cleanup.

| # | Check (hosted) | Result |
| --- | --- | --- |
| 1 | Dashboard state counts | DEMONSTRATED 1 · DEVELOPING 1 · EMERGING 1 · UNKNOWN 21 (24 course skills) |
| 2 | UNKNOWN remains neutral | 21 UNKNOWN with a null mean; only the delegated skill has a ledger row; debt band NONE except the eligible one; neutral tile and badge in the UI |
| 3 | Skill map overlays ledger state | all 24 graph skills carry a state; the 5 roles as expected; UI: 5 topics, 24 rows |
| 4 | Skill detail + Why? provenance | all states explained; 4/4 gates; 13/13 events traced to raw messages and activity chips; 90% / 95% confidences; no prompt or policy snapshot |
| 5 | Debt explanation | HIGH (importance 0.8), 5 factors, ELIGIBLE, UNVERIFIED; the others NONE; the score only in internal detail |
| 6 | Activity enrichment | 26 rows, 13 anchors (MAP/MAPPED), actors AI + STUDENT, types OBSERVATION + INDEPENDENT_APPLICATION, 1 excluded |
| 7 | DONT_COUNT (in the browser) | feedback stored; evidence excluded (`LEARNER_DONT_COUNT`); DEMONSTRATED → DEVELOPING; `ledger_version` 2 → 3 |
| 8 | WRONG_SKILL (in the browser) | feedback stored on the mapping; evidence excluded (`LEARNER_WRONG_SKILL`); mapping still ACCEPTED; provenance 26/13/13/13/13/13 unchanged |
| 9 | Recommendations refresh deterministically | the same queue on repeat: VERIFY 65 → PREREQUISITE 59 → PRACTICE 49 → PRACTICE 39; after a third WRONG_SKILL the debt is no longer eligible and VERIFY → NO_ACTION |
| 10 | Retrying feedback is idempotent | replay 200 with the same id; same target under a new key 200 with the same id; key reuse with another body 409; feedback rows unchanged |
| 11 | Zero generation calls | `model_runs` generation 27 → 27 |
| 12 | Zero embedding calls | `model_runs` embedding 7 → 7 |

**Browser walkthrough (hosted):** 1 passed (38 s).
- The first attempt timed out on a cold dev-compiled page (5.6 s against the default 5 s), before
  any correction. The spec now waits up to 30 s.
- The final dashboard screenshot of that run caught a transient loading skeleton, after its
  assertions had passed. The spec now waits it out.

## Local acceptance P5 (2026-09-25): PASS, no model call

Full local stack: local Supabase (Auth + Postgres, migrations 0001–0007) → local FastAPI :8001
**without `GEMINI_API_KEY`** (no ModelGateway, no worker) → local Next.js :3001. Script:
`services/backend/scripts/acceptance_p5.py`; browser walkthrough: `apps/web/e2e/p5-acceptance.spec.ts`.
Evidence: `test-results/p5-acceptance/evidence.json` + 8 screenshots (git-ignored; ids only).

Two fresh learners were signed up through Supabase Auth (real JWTs) and seeded with the
deterministic P5 fixtures (`services/backend/tests/p5_fixtures.py`). The fixtures run the
production evidence qualification, persistence, ledger and recommendation code; no model run
exists for them. Each learner has one course and 5 skills in 2 topics:

- for loops: DEMONSTRATED, 4 independent applications, plus 1 excluded by DONT_COUNT
- while loops: UNKNOWN, no ledger row
- comprehensions: UNKNOWN, debt eligible (3 AI delegations, MODERATE ≈ 20)
- variables: EMERGING
- indexing: DEVELOPING, with an EMERGING prerequisite

**API checks (learner A, over HTTP):**

| # | Check | Result |
| --- | --- | --- |
| 1 | Dashboard counts | DEMONSTRATED 1 · DEVELOPING 1 · EMERGING 1 · UNKNOWN 2 |
| 2 | UNKNOWN neutral | null mean for both; the row-less skill has no ledger version |
| 3 | Skill map overlay | all 5 assessable graph skills carry a state; the row-less skill is UNKNOWN |
| 4 | Every state explained | INDEPENDENT_EVIDENCE_SUPPORTS / NO_EVIDENCE / NO_INDEPENDENT_PERFORMANCE / EARLY_DIFFICULTY / MIXED_RESULTS, with NO_ACTION / NO_ACTION / VERIFY / PRACTICE / PREREQUISITE; no prompt or policy snapshot in any response |
| 5 | Evidence traces to activity | 13/13 events → their raw messages → the activity chip with the same evidence id |
| 6 | Debt without moral judgement | MODERATE with 5 factors; the other skills NONE; no judgemental wording |
| 7 | DONT_COUNT | 201; DEMONSTRATED → DEVELOPING; `ledger_version` 2 → 3; replay 200 with the same feedback id |
| 8 | WRONG_SKILL | 201; 1 evidence excluded; raw/segments/decisions/mappings/attributions/evidence 26/13/13/13/13/13 before and after |
| 9 | Recommendations deterministic | queue VERIFY → PREREQUISITE → PRACTICE → PRACTICE, identical on repeat; after a second WRONG_SKILL the debt is no longer eligible and VERIFY → NO_ACTION |
| 10 | No model request | `model_runs` 347 → 347 |

**Browser walkthrough (learner B):**
1. Sign-in, then the dashboard: counts, neutral UNKNOWN tile first, VERIFY first in the queue.
2. Skill Map: 2 topics, 5 skills; the row-less skill shows "Not enough activity yet", neutral tone.
3. Skill Detail with the mastery "Why?" (4 gates met), then an evidence "Why?" (span, 90% / 95%,
   `STUDENT_WROTE_CODE`).
4. Source activity (the focused rows).
5. Debt panel: "Moderate reliance signal", 5 factors, the score hidden in internal detail.
6. "Don't count this": the mastery badge becomes Developing and the recommendation "Build
   consistency".
7. "Wrong skill" on an activity chip: marked "Marked wrong skill".
8. Dashboard again: DEMONSTRATED 0, DEVELOPING 2, 4 recommendations.

Result: **1 passed**.

## Live acceptance P3B + P4 (2026-09-25): PASS on `gemini-3.5-flash-lite`

Local backend (`uvicorn` :8000, in-process worker, combined mode) → hosted Supabase
(0001–0006) → Google AI API. The run reused the READY course `9440004a-a25e-4e15-94c0-17c21f6bd695`
(24 skills) and its owner; no graph was generated and there were no open jobs beforehand.
Script: `services/backend/scripts/acceptance_p3b_p4.py`, which was first dry-run locally
against the scripted fake provider. Evidence: `test-results/p3b-p4-acceptance/evidence.json`
(git-ignored; ids only).

**Runtime-only overrides (not committed):**
- `GEMINI_GENERATION_MODEL=gemini-3.5-flash-lite`
- `GEMINI_GENERATION_RPM=12`
- `MODEL_DAILY_REQUEST_LIMITS=gemini-3.5-flash-lite=500,gemini-3.7-flash=20,gemini-3.8-flash=20`
- `MODEL_QUOTA_RESERVE=25`

**The turn**, ingested through the ingestion service: *"I wrote this myself to print every
name in my list: `for name in names: print(name)`. Now can you write the version that also
prints each name's position for me?"* The assistant confirms the loop and writes the
`enumerate` version.

**Chain:**
1. **Job:** `COMPLETED` / `EVIDENCE_RECORDED`, attempt 1.
2. **Model runs (all real provider requests):**
   - `EMBED_QUERY` (gemini-embedding-2)
   - `TURN_ANALYSIS` (turn-analysis/v1; 2 646 tokens)
   - `SKILL_ATTRIBUTION` (skill-attribution/v1; 1 341 tokens)

   Both generations succeeded at attempt 1, with no repair and no adjudication.
3. **P3A:** MAP (high relevance) → MAPPED → *Writing for loops over sequences* ACCEPTED at 0.90
   (`FIRST_PASS_ACCEPTED`), span `for name in names: print(name)`.
4. **P3B attribution:** STUDENT, confidence 0.95, INDEPENDENT_APPLICATION, CORRECT,
   `STUDENT_WROTE_CODE`, learner span `for name in names: print(name)` → `EVIDENCE_CREATED`.
5. **EvidenceEvent:** `AI_ACTIVITY`, INDEPENDENT_APPLICATION, STUDENT, CORRECT, outcome 1.0.
   - Strength **0.7875** = base 1.0 × difficulty multiplier 0.875 (band 2) × independence 1.0 ×
     min(0.90, 0.95).
   - `source_id = attribution_id`; `raw_message_ids` = the segment's two raw messages.
   - All 3 `model_run_ids` exist.
6. **Ledger (P4):** α 1.7875, β 1.0, support 0.7875 < 1.0 → **UNKNOWN** (one piece of evidence
   is not enough; the API shows no mean). Debt: **not eligible, score 0** (`NO_DELEGATION`).
7. **AI usage without debt.** The learner used the AI in this turn (it wrote the `enumerate`
   version at their request), and the ledger shows no debt. The mapper did not map a separate
   skill for that request, so there is no AI-actor evidence event in this live run. The
   AI-actor "single delegation → no debt" path is proven by the DB pipeline test and the
   safety benchmark (`join-syntax-once`, `calculator-once`, `give-me-the-answer`,
   `copied-ai-answer`).
8. **Replay** (the same job, in-process): `ALREADY_ANALYZED`, **0 provider requests**;
   model runs / attributions / evidence 3/1/1 → 3/1/1.
9. **Ledger idempotency.**
   - That replay found a decay-only change in the 6th decimal (α 1.787499, `ledger_version` 2):
     recency age was then fractional days.
   - Fixed in this branch: age is now counted in **whole UTC days**, so a same-day recompute
     yields the identical row.
   - Re-verified live: two rebuilds of the learner's ledger → α 1.7875, `ledger_version`
     3 → 3, 0 model runs.

**Hosted, read-only, after the run:** 1 attribution, 1 evidence event and 1 ledger row in
total; no open jobs. Provider requests of the Pacific day: Flash-Lite 5 (P2/P3A 3 + P3B/P4 2)
of 500, reserve 25 intact; `gemini-3.7-flash` 6 (all from the earlier P2/P3A attempts);
`gemini-embedding-2` 6.

## Live acceptance P2 + P3A (2026-09-25, second run): PASS on `gemini-3.5-flash-lite`

Local backend (`uvicorn` :8000, in-process worker, combined mode, `GEMINI_GENERATION_RPM=4`) →
hosted Supabase (migrations 0001–0004) → Google AI API. The run resumed the pending course
`9440004a-a25e-4e15-94c0-17c21f6bd695` and the pending P1 turn; no data was regenerated.
Evidence: `test-results/p2-p3a-acceptance/evidence.json` (git-ignored; ids only).

**Runtime overrides (not committed):**
- `GEMINI_GENERATION_MODEL=gemini-3.5-flash-lite`
- `MODEL_DAILY_REQUEST_LIMITS=…,gemini-3.5-flash-lite=10`. Its RPD cannot be read without OAuth
  (no `gcloud`; the Gemini API exposes no rate limits). This value is a **local spending cap**
  for the run, with the reserve of 2, not a claim about Google's quota. *(Later the same day the
  owner read the real limit in AI Studio: 500 RPD; see *Gemini free-tier limits*.)*

**Sequence and provider requests:**

1. **Quota check** (`gemini-3.7-flash`): three minimal gateway requests (07:20, 07:24, 07:34 UTC)
   gave 503, 503, then `SUCCEEDED`. The daily quota had reset, and capacity was the problem.
2. **3.7 attempts**: the worker's first attempts at the pending turn and at the graph, plus one
   scoped retry at 07:56 UTC, all got 503. The jobs were deferred with **0 attempts spent**. The
   worker was stopped so the budget would not drain while waiting.
3. **Switch** to `gemini-3.5-flash-lite` (owner decision). One production worker round on the
   bootstrap job:
   - graph: **1 generation request** (33 s, 4 056 tokens, attempt 1, no repair)
   - canonicalization: 24 skills, 5 topics, 48 aliases, 66 edges
   - **1 embedding request** for all 24 skills; the course is `READY`
4. **Pending P1 turn** ("Explain binary search in one sentence."): 1 query embedding + **1
   `TURN_ANALYSIS`** request, attempt 1. The `turn-analysis/v1` schema was **accepted by the real
   Gemini API**. Result: `STOP` (`NON_LEARNING`), relevance low@0.8, intent understand; the job
   is `COMPLETED`.
5. **Retrieval probe**: 1 query embedding, **0 generation**. The pool of 20 is built locally, with
   9 lexical matches and all 20 cosine-scored. The top 3 are *Creating and indexing lists* (0.804),
   *Using list methods*, and *Writing for loops over sequences*.
6. **Acceptance turn** (a `range(len(...))` loop → `enumerate`): 1 query embedding + **1
   `TURN_ANALYSIS`** request, and no adjudication (both mappings were ≥ 0.80).
   - route: `MAP` (academic / learn / high@1.0 / skill-bearing@1.0 / `CODE_REQUEST`)
   - pool 20 → top-8 → `MAPPED`
   - *Writing for loops over sequences*: 0.90, `FIRST_PASS_ACCEPTED`, span
     `for i in range(len(names)):`
   - *Creating and indexing lists*: 0.80, `FIRST_PASS_ACCEPTED`, span `names[i]`
   - `rerank_model_run_id = mapping_model_run_id = qualification_model_run_id`: one call
   - `prompt_versions` = `{query_embedding, turn_analysis, adjudication}`; `mapper/p3a-turn-v1`
7. **Identical repeat** (the same turn in a new conversation, the same worker process): the query
   embedding and `TURN_ANALYSIS` were both served from the cache, with the same `MAPPED`
   decision. **Provider requests during the repeat: 0.**
8. **Budget after the run:** `gemini-3.5-flash-lite` 3/10 used, reserve intact.

**Verified on hosted (read-only queries):**
- **Provenance to raw activity.** Each segment's `source_message_ids` resolve to the user and
  assistant `raw_messages`. Each decision → its query-embedding and turn model runs →
  prompt versions and the policy snapshot.
- **Cache provenance.** Each hit row has `cache_source_run_id` pointing to a provider-produced
  `SUCCEEDED` run, with an equal `cache_key` and an identical `output`.
- **Failures never cached.** The 5 failed runs of the day (503, `UNAVAILABLE`) carry no
  `cache_key`.
- No evidence, ledger or attribution tables exist (P3A creates none).

**Provider requests of the day, cache hits excluded:**
- `gemini-3.7-flash`: 6 (the three quota checks and three worker attempts: five 503s, one success)
- `gemini-3.5-flash-lite`: 3 (graph, P1 turn, acceptance turn)
- `gemini-embedding-2`: 5
- `gemini-3.8-flash` (second fallback): not needed, 0

**Not verified live in this run:**
- **An adjudication.** No real mapping landed in 0.65–0.79. The adjudication path is covered by
  tests (1 extra request, at most 1 per turn).
- **Any `gemini-3.7-flash` generation beyond the minimal check**, because of the 503s.

## Live acceptance, first run (2026-09-25, before ADR 0004): partial, stopped at the free-tier quota

Local backend (`uvicorn` :8000, in-process worker) → hosted Supabase → Google AI API.

**Defects found live and fixed** (`20dbd77`):

1. **HTTP 400 "Request contains an invalid argument"** on structured calls. A live bisect
   located it: array bounds (`minItems`/`maxItems`) combined with enums exceed Gemini's
   schema-complexity limit. Removing either one fixes it. The provider schema now drops array
   bounds; Pydantic still enforces them.
2. **429 (free-tier quota) and 503 ("high demand")** would have burnt the job's 3 attempts. They
   are now transient: the server's `retryDelay` is parsed, and the worker defers the job
   (`MODEL_BACKPRESSURE`) without spending an attempt. Optional client-side limits:
   `GEMINI_GENERATION_RPM` / `GEMINI_EMBEDDING_RPM`.
3. **Reason-code casing** (`programming_question`) cost a repair call. Codes are now folded to
   UPPER_SNAKE before strict validation. Automatic function calling is disabled.

**Verified live:**

- All five engine schemas were accepted by the Gemini API, with outputs that validate strictly:
  qualification (after 3), graph, rerank, mapping, adjudication. This ran on
  `gemini-3.8-flash` (temporary override), minimal prompts.
- `gemini-embedding-2`: separate 768-d vectors per text; the semantic ordering is sensible.
- A fresh learner (public Auth signup) created **course `9440004a-a25e-4e15-94c0-17c21f6bd695`**
  ("Introduction to Python Programming", Beginner) through `POST /v1/courses` on hosted.
  Membership and bootstrap job were created in the request; no model call was made during it.
- The worker claimed the bootstrap job and the P1 turn's job and made real calls. Every call was
  logged in `model_runs` (1 × 400 on 3.7-flash; 13 × 429 and 1 × 503 on 3.8-flash). Both jobs were
  deferred on backpressure with **0 attempts spent**, and nothing reached `FAILED`.

**Not yet verified live (the remaining gate):**

- real graph generation for a course, and its stored embeddings
- retrieval and top-20 → top-8 rerank over a real graph
- a captured turn through qualification → mapping/abstention, with provenance

Hosted state waiting for the quota reset:
- course `9440004a…`: `GENERATING`, job `PENDING` (`MODEL_BACKPRESSURE`, 0 attempts)
- the P1 turn (`Explain binary search in one sentence.`): assistant job `PENDING`
  (`MODEL_BACKPRESSURE`, 1 attempt: the first 400)

**Temporary override record:** `GEMINI_GENERATION_MODEL=gemini-3.8-flash` was set only in the
environment of this acceptance run. The code default, `.env.example`, ADR 0003 and
`policy_config` still name **`gemini-3.7-flash`**. A live `gemini-3.7-flash` rerun remains a
validation item after its quota resets. The 3.7 live acceptance has **not** passed.

**Smoke benchmark:** the 12-case `p3a-smoke` set needs about 36 generation calls, which exceeds
the free tier's 20/day. Its schema and scorer are covered deterministically in CI; the live run
is deferred. *(That 20/day is the `gemini-3.7-flash` limit; on Flash-Lite, 500 RPD, the run fits.)*

## URLs and versions (local)

| Item | Value |
| --- | --- |
| Web | http://localhost:3000 (`/courses`, `/courses/new`, `/courses/{id}`; P5: `/dashboard` (courses, state counts, recommendations), `/skills`, `/skills/{id}`, `/activity` (enriched + corrections); P6: `/verifications` (Verification Center), `/verifications/{id}` (challenge, result); **P7: `/teacher`, `/teacher/courses/{id}`, `/admin`, `/admin/jobs`, `/admin/model-runs`, `/admin/skill-candidates`, `/admin/benchmark`**) |
| API | http://localhost:8000: `/health`, `POST/GET /v1/events/…`, `POST /v1/courses`, `GET /v1/courses`, `GET /v1/courses/{id}`, `GET /v1/courses/{id}/skills`, `GET /v1/ledger[?course_id=]` (P4, + `debt_band`), P5: `GET /v1/skills/{id}`, `GET /v1/activity`, `POST /v1/feedback` (Idempotency-Key), `GET /v1/recommendations`; **P6: `GET /v1/verifications` (plans, no model call), `GET /v1/verifications/{id}`, `POST /v1/verifications/{id}/start`, `POST /v1/verifications/{id}/submit` (Idempotency-Key), `POST /v1/verifications/{id}/abandon`**; **P7: `GET /v1/me`, `GET /v1/teacher/courses`, `GET /v1/teacher/courses/{id}/overview`, `/v1/admin/{overview, jobs[/{id}], jobs/{id}/retry, model-runs, skill-candidates, skill-candidates/{id}/review, benchmark[/{id}], courses[/{id}], courses/{id}/members, skills[/{id}]}`**; OpenAPI `/docs` |
| Worker | in the API process when `DATABASE_URL` + `GEMINI_API_KEY` are set; or `python -m app.jobs.worker [--once]` |
| Deployed web / API | none, by decision (local-first) |
| Extension version | 0.2.0, dev id `cohpimnabjigooghbigblennedbplojm` (unchanged in this phase) |
| Backend version | 0.1.0 |

## Automated results (local run on 2026-09-26, Windows, Node 22.14, Python 3.13)

| Suite | Local | CI job |
| --- | --- | --- |
| Backend `ruff check` + `ruff format --check` (incl. benchmark runner) | clean | Backend |
| Backend pytest, unit (no DB) | **874 passed, 172 skipped** (P7: 817 / 152) | Backend |
| Backend pytest, with local Postgres (0001–0009) | **1046 passed**, incl. the critical gate deterministic 120/120 and replay 72/72 (P7: 969) | Backend ingestion + intelligence + database |
| Database pgTAP (0001–0009) | **363 passed** (13 + 33 + 49 + 10 + 40 + 15 + 49 + 79 + 75) | Database |
| P3B/P4 safety benchmark (`benchmark/runners/p3b_p4_safety.py`, 22 deterministic cases) | **22/22**; False AI Assistance Debt Rate **0/21**; debt recall 2/2 | Backend (`test_benchmark_safety.py`) |
| Web ESLint | 0 problems | Web |
| Typecheck (web, contracts, config, ui, extension) | 5/5 clean | Web, Extension |
| Web vitest | **106 passed** (P7: 96) | Web |
| Web production build (no env) | pass | Web |
| Extension vitest | **100 passed** (P7: 86) | Extension |
| Extension build + manifest validation + Chromium (load ×2, capture → queue → sync, active-course picker) | pass, **4 passed** | Extension |
| Local P7 acceptance (no model call) | **PASS**: prepare 4/4, browser 3/3, verify 16/16, cleanup 3/3 | not in CI by design |
| Local P6 acceptance (scripted fake provider) | **PASS**: 15/15 + browser walkthrough (1 passed); 0 real model calls | not in CI by design |
| Hosted P6 acceptance (real Gemini) | **PASS on `gemini-3.5-flash-lite`**: 12/12 + browser walkthrough; 1 generation / 0 evaluation / 0 embedding; REAL LIVE PROOF; disposable learner deleted; real learner untouched | not in CI by design |
| Local P5 acceptance (no model call) | **PASS**: 10/10 API checks + browser walkthrough (1 passed); `model_runs` unchanged | not in CI by design |
| Hosted P5 acceptance (no model call) | **PASS**: 12/12 + browser walkthrough; disposable learner deleted; real learner untouched; 0 generation / 0 embedding | not in CI by design |
| Real Gemini acceptance (P3B + P4) | **PASS on `gemini-3.5-flash-lite`**: 1 TURN_ANALYSIS + 1 SKILL_ATTRIBUTION + 1 embedding; replay 0 requests | not in CI by design |
| Real Gemini acceptance (P2 + P3A) | **PASS end to end on `gemini-3.5-flash-lite`** (3 generation requests; identical repeat 0); `gemini-3.7-flash` pending (503) | not in CI by design |
| P3A smoke benchmark (`benchmark/runners/p3a_smoke.py`, 12 cases) | live run **deferred**, per the owner (about 12–16 requests in combined mode) | schema + scorer only in CI |
| Real acceptance script (`services/backend/scripts/acceptance_p2_p3a.py`) | **passed** with `--resume-course`; prints the budget before and after; checks the identical repeat (0 provider requests) | not in CI by design |

New backend test modules (ADR 0007): `test_verification_db` (30), `test_verification_planner`, `test_verification_validator`, `test_verification_generator`, `test_verification_grading`, `test_mastery_verification`, `test_debt_verification`, the `p6_fixtures` seed; parity extended to the P6 enums, models and the A.5 `pass` alias. Web: `verification.test.ts`, `verification.test.tsx`.
ADR 0006: `test_experience_db` (18), `test_recommendations_engine`, `test_zero_model_calls`, `test_migrations_frozen`, the `p5_fixtures` seed; parity extended to the P5 enums and models. Web: `experience.test.ts`, `experience.test.tsx` (server-rendered components).
ADR 0005 modules: `test_attribution`, `test_evidence_qualification`, `test_mastery`,
`test_debt`, `test_evidence_pipeline_db`, `test_ledger_api`, `test_benchmark_safety`. The P3A pipeline
tests run the P3A stage alone (`evidence=False`).
ADR 0004: `test_model_gateway_free_tier`, `test_turn_analysis`, `test_gateway_db`.
Earlier: `test_model_gateway` (27), `test_skill_graph_unit`, `test_retrieval_scoring`,
`test_qualification`, `test_mapping`, `test_courses_api`, `test_policy_db`, `test_skill_graph_db`,
`test_retrieval_db`, `test_pipeline_db`, `test_worker_db`, `test_contract_parity`, `test_benchmark_smoke`.

## CI

P7 + P8 (PR #8): the PR run on head `f476bf0`
([36177071898](https://github.com/shaka9lakaboom/lappu1/actions/runs/36177071898)) and the `main`
push of the merge `01e828c`
([36177455267](https://github.com/shaka9lakaboom/lappu1/actions/runs/36177455267)) both completed
with success.

Existing jobs are extended; no job was added. P6: the backend job runs the P6 unit tests (planner,
validator, generator, graders, mastery/debt verification); backend integration runs
`test_verification_db`; the database job runs pgTAP 0008; the web job runs the verification
vitest. No job has a Gemini key: generation and evaluation use the scripted fake provider, and
`test_zero_model_calls` pins the HTTP paths as gateway-free. P3B + P4:
- The backend job runs the safety benchmark and the new unit tests.
- Backend integration runs the evidence pipeline and ledger API tests.
- The database job runs pgTAP 0005 + 0006.

Earlier:
- The backend job lints `benchmark/runners`.
- Backend integration now covers courses, the skill graph, retrieval, the P3A pipeline and the worker.
- pgTAP includes 0003 and 0004.
- The hygiene scan also rejects Google API keys.

No job needs `GEMINI_API_KEY`: intelligence tests use a scripted fake provider.
Run [36070884068](https://github.com/shaka9lakaboom/lappu1/actions/runs/36070884068) (`c723960`)
failed backend lint (one unformatted test file) and was cancelled by the fix. Run
[36071071084](https://github.com/shaka9lakaboom/lappu1/actions/runs/36071071084) (`2801ed4`)
passed all 7 jobs: hygiene, backend, backend integration, web, extension, database,
auth round trip.

## Known defects and caveats

- **Attribution / evidence consistency (hosted, 2026-09-25) — fixed and merged (PR #8), proven
  live on hosted with disposable data, hosted row remediated** (ADR 0008 §24–26; see *Hosted E2E
  smoke P8*: `affected` 8/8). Left: the lost explanation (immutable) and the historical READY
  check `3aa4481e…` (a P9 UX cleanup item). The text below is the original analysis. A learner's own loop + explanation, checked by the AI,
  showed "Actor: You" next to "The AI did it", then a high reliance signal, VERIFY and a READY check.
  Cause: the copy guard (the loop was in an earlier AI answer) recorded the evidence as the AI's; the
  activity chip showed the attributor's claim instead; the reclassified evidence counted as a second
  delegation (false debt); the attributor was never told which text was reused, so the learner's
  explanation was lost. Fixed: activity actor = evidence actor (+ `attributed_actor`,
  `qualification_reason`); `ledger/p8-v1` (a copy-guard reclassification is not a delegation);
  `skill-attribution/v2` (reused text listed; own explanation quoted) plus the attribution validator
  (a span the copy guard would reclassify is refused, one repair). Hosted: nothing written. After the
  merge and a restart on the fixed code, the worker's startup sweep re-derives the row (older ledger
  algorithm); `scripts/recompute_skill.py` shows the change first (dry run for learner `8afbd2c8…` /
  skill `2761328b…`: debt 30.1 → 0, delegations 2 → 1). The lost explanation cannot be restored
  (immutable evidence). The capture itself was correct: all 8 messages of that conversation came
  through `chatgpt-2` (extension 0.2.0), in order, replies paired.
- **P6, see ADR 0007 *Known limitations*:**
  - Free-text answer keys cannot be proven correct deterministically; MCQ / numeric keys are
    structurally checked and free text is graded against the rubric.
  - Duplicate detection is lexical (word Jaccard + number masking, history of 5 prompts).
  - One item per session; no adaptive or multi-item checks yet.
  - Contradiction counts only INCORRECT independent results (≥ 2); a drift of PARTIAL results
    lowers the mean without triggering re-verification.
  - `GET /v1/verifications` refreshes recommendations and plans before reading (idempotent cache
    and session writes in a GET, like P5's recommendation refresh).
  - A timezone change mid-day can shift the daily budget by one day.
  - **Live coverage is one MCQ challenge, deterministically graded.** The rubric evaluator
    (`verification-evaluation/v1`), regeneration after a validator rejection and a live FAILED
    result are proven with scripted providers (tests + local acceptance), not live.
  - The hosted VERIFIED came from one live pass on top of a deterministic pre-pass fixture sized
    so that one pass could meet the gates (the REAL LIVE PROOF case; nothing was added after the
    pass). A learner with less prior evidence correctly stays below VERIFIED after one pass.
- ~~**Backend JWT `iat` has no leeway (pre-existing, P0 auth)**~~ — **fixed in P7** (ADR 0008 §21):
  a bounded 5 s clock-skew leeway (max 30 s); tests prove +3 s accepted, +60 s and a 10 s-expired
  token rejected.
- **Hosted pooler connections can drop.** One fixture connection was closed by the server
  mid-transaction during the hosted P6 run and left an orphaned idle-in-transaction backend. The
  product's writes are single transactions (a dropped connection rolls back); the acceptance
  seeding now uses one transaction per turn.
- **P5, see ADR 0006 *Known limitations*:**
  - A learner can exclude any of their captured-activity evidence, including unfavourable
    evidence (the architecture's "don't count this"). Each correction is kept as an append-only
    feedback row with its effect.
  - Corrections cannot be undone (one-way by design). WRONG_SKILL does not remap yet, and a
    recommendation cannot be dismissed yet.
  - `GET /v1/recommendations` and `GET /v1/skills/{id}` refresh the derived queue before reading
    (an idempotent cache write in a GET).
  - The VERIFY cap (2) is concurrent; the daily challenge budget belongs to the P6 planner.
  - The debt bands (15/25) and factor thirds are engineering defaults (calibration P8).
  - The local acceptance script creates prefixed registry rows (its fixture builds a graph).
    Run it on a disposable local database and `supabase db reset` afterwards; otherwise the
    retrieval DB tests see the extra skills. The hosted script reuses the existing graph and
    creates none.
  - The hosted acceptance used deterministic fixtures on the real course graph. No new live
    ChatGPT turn went through P5 (P5 makes no model call; P3B/P4 already proved the pipeline live).
- **P3B/P4, see ADR 0005 *Known limitations*:**
  - A ledger row only decays when its skill is recomputed, which happens on new evidence;
    `computed_as_of` shows when.
  - The copy guard sees only the recent context window (4 messages).
  - Span grounding is lexical: a paraphrased learner span abstains.
  - Delegation depends on the model's OBSERVATION/EXPOSURE and reason-code choice. The
    ≥ 2-event and 0.80-confidence gates bound a wrong call; calibration is P8.
  - Difficulty uses the skill's band, not the task's.
- **Live P3B/P4 coverage is one turn**, with one STUDENT attribution. AI/SHARED attribution,
  debt eligibility and abstention are proven with scripted providers (tests + safety
  benchmark), not live.
- **Turns processed before migration 0005** (the P2/P3A acceptance turns) have P3A rows but no
  attribution. A replay of their jobs would attribute them (1 request each); nothing does so
  automatically.
- **Gemini free tier (owner-verified 2026-09-25, see *Gemini free-tier limits*):** `gemini-3.5-flash-lite` 15 RPM / 500 RPD; `gemini-3.7-flash` and `gemini-3.8-flash` 5 RPM / 20 RPD; `gemini-embedding-2` 100 RPM / 1000 RPD. With ADR 0004 a normal turn needs 1 generation request (ambiguous: 2), and the smoke benchmark about 12–16 instead of about 36. Jobs defer instead of failing while over quota or at the budget reserve.
- **`turn-analysis/v1` is accepted live on `gemini-3.5-flash-lite`**, twice, at attempt 1 each. It has not been exercised on `gemini-3.7-flash` yet. If 3.7 ever rejects it, set `TURN_ANALYSIS_MODE=staged` (no code change).
- **`gemini-3.7-flash` live validation is pending.** On 2026-09-25, after the quota reset, 5 of 6 requests got HTTP 503 "high demand". Every 503 counts toward the local budget, so the worker was stopped instead of letting jobs cycle.
- **Flash-Lite graph size:** its graph has 24 skills, inside the 20–80 hard bounds but below the 30–60 target. The P1 turn "Explain binary search in one sentence." was rated low relevance (`STOP`). Both are quality observations for the P8 benchmark and calibration, not gate failures.
- **Combined mode retrieves once per unit.** A multi-task turn shares one top-20 pool between its segments, and a non-learning turn still costs one query embedding.
- **Query vectors are cached in memory only.** After a restart, an identical turn costs 1 embedding request; the generation still comes from the durable cache.
- **`gemini-3.5-flash-lite` quota** (corrected 2026-09-25): the owner read it in AI Studio: 15 RPM / 250K TPM / 500 RPD. It is the hackathon operational runtime, set in the environment only; routing stays off by default in the code, and the architecture default stays `gemini-3.7-flash`.
- **Real-model quality is only spot-checked** (one graph, two turns). Mapping calibration and the adjudication band need the benchmark (P8).
- Hosted Auth: *Leaked Password Protection* is disabled (advisor WARN; an owner setting).
- Lexical scores are relative to the best match in the pool (relative score fusion). A pool of
  uniformly weak lexical matches still gives its best one a lexical score of 1.0. The semantic
  term and the mapper gate bound the effect. Tune against the benchmark (P8).
- A user message whose assistant reply is captured more than 120 s later is analysed alone. The
  late reply's job then completes as `ALREADY_ANALYZED`, without re-analysis.
- `raw_messages.active_course_id` has no FK (append-only table). It is validated at processing time.
- The extension does not send `active_course_id` yet. Course context is then all of the learner's
  courses (at most 5).
- `skill_candidates` review: **done in P7** (`/admin/skill-candidates`, APPROVE / MERGE / REJECT).
  Regenerating a READY course graph from the UI stays a limitation (V1.2); a FAILED bootstrap is
  retried from `/admin/jobs` (resuming at its stage).
- Carried over from P1: the signed-in ChatGPT layout is covered only by a synthetic fixture; there
  is no "capture degraded" popup state yet (P8); auto-confirm is on for the hosted project.

## Required environment variables (names only)

- Web (`apps/web/.env.local`): `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`,
  `NEXT_PUBLIC_API_URL` (required for the Courses pages)
- Backend (`services/backend/.env`):
  - existing: `APP_ENV`, `APP_NAME`, `API_VERSION`, `CORS_ORIGINS`, `SUPABASE_URL`, `DATABASE_URL`,
    `SUPABASE_JWT_SECRET` (legacy HS256 only)
  - **new:** `GEMINI_API_KEY` (server only), optional `GEMINI_GENERATION_MODEL`,
    `GEMINI_EMBEDDING_MODEL`, `GEMINI_THINKING_LEVEL`, `MODEL_TIMEOUT_SECONDS`, `GEMINI_GENERATION_RPM`, `GEMINI_EMBEDDING_RPM`, `WORKER_ENABLED`,
    `WORKER_POLL_SECONDS`, `WORKER_BATCH_SIZE`, `WORKER_STALE_AFTER_SECONDS`
  - **ADR 0004 (all optional):** `GEMINI_ROUTINE_MODEL`, `GEMINI_ROUTINE_THINKING_LEVEL`,
    `MODEL_DAILY_REQUEST_LIMITS`, `MODEL_QUOTA_RESERVE`, `MODEL_QUOTA_TIMEZONE`, `MODEL_RESULT_CACHE`,
    `MODEL_RESULT_CACHE_MAX_ENTRIES`, `TURN_ANALYSIS_MODE`
  - **ADR 0005:** no new variable. The hackathon operational runtime sets the existing
    `GEMINI_GENERATION_MODEL` / `GEMINI_ROUTINE_MODEL`, `GEMINI_GENERATION_RPM`,
    `MODEL_DAILY_REQUEST_LIMITS` and `MODEL_QUOTA_RESERVE` in the process environment only.
  - **ADR 0006 (P5):** no new variable. P5 needs no `GEMINI_API_KEY`. The course selector
    is a UI cookie (`sm_course`), not configuration.
  - **ADR 0008 (P7):** no new variable. Roles are granted by the operator with
    `scripts/grant_role.py` (DATABASE_URL); the teacher view reads `policy_config.teacher_view`.
  - **ADR 0007 (P6):** no new variable. The HTTP paths need no `GEMINI_API_KEY`; the worker
    needs it for `GENERATE_VERIFICATION` (and `GRADE_VERIFICATION` of free-text answers). The
    verification policy lives in `policy_config.verification`, not the environment.
- Extension build (optional, public values): `SKILLMIRROR_SUPABASE_URL`, `SKILLMIRROR_SUPABASE_ANON_KEY`,
  `SKILLMIRROR_API_URL`, `SKILLMIRROR_WEB_URL`
- Tests: `TEST_DATABASE_URL`, `REQUIRE_DB_TESTS`, `UPDATE_GOLDEN`

## Exact next action

**P7 COMPLETE. P8 COMPLETE.** Merged (`main` `01e828c`); hosted has 0001–0009; the hosted E2E
smoke passed; the three benchmark runs are recorded; the hosted remediation of the 2026-09-25 row
is COMPLETE (`affected` 8/8). Do not run `recompute_skill.py --apply` for that row again.
**P9 — Local Demo Integration + Final Hardening is not started.** Its branch
`skillmirror-p9-final-demo-hardening` (from `main` `01e828c`) holds only the smoke script, the
smoke walkthrough and these records so far.

1. **P9 starts in its own session.** Carried into P9:
   - **UX cleanup: the historical READY check `3aa4481e…`** (learner `8afbd2c8…`, "Writing for loops
     over ranges"). It stays READY while its VERIFY `e8d6092f…` is superseded. Decide how the
     verification centre shows (or lets the learner dismiss) a check whose reason is gone. Until
     then nothing changes the session.
   - The first `npm run demo` on `main` sweeps: it re-tags 3 rows of `8afbd2c8…` and the real
     P3B/P4 learner's one row, and inserts 24 NO_ACTION recommendations for the real learner
     (previewed, rolled back; no model call).
   - The E2E CI job on the replay provider (H13); `skill-attribution/v3` for Flash-Lite's false
     credit of questions (ADR 0008 §35).
2. **Run the demo only from `main`.** The old `lappu1-demo-hotfix` checkout (`00ff0a4`, `ledger/p6-v1`)
   would re-derive a row with the old algorithm on that skill's next evidence.
3. **Owner approvals still open:**
   - `RESUME_ATTRIBUTION` for the pre-0005 hosted turns (K7, ≤ 2 requests)
   - the recorded signed-in ChatGPT fixture and the `SIGNED_IN=1` live check (H10: needs the owner
     signed in)
   - the `gemini-3.7-flash` canary was skipped by the owner (not planned)
