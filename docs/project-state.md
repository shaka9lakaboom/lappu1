# SkillMirror — Live Project State

This record carries live implementation status (architecture §0.1). It must never
claim an unverified gate. Architecture: [`architecture/`](architecture/). Decisions:
[`decisions/`](decisions/) (0001 P0, 0002 P1, 0003 P2 + P3A, 0004 free-tier ModelGateway,
0005 P3B + P4).

**Last updated:** 2026-09-25

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
| Default branch | `main`. P0, P1, P2 + P3A and P3B + P4 are merged. **`main` = `ab2d73d6e43f3c0122a08506ffedcd62c5ff19a8`** (merge of PR #4). |
| P1 merge commit | `5ee62d01cb40ffac3dbf9342456935092a944098` (PR #2) |
| P2 + P3A merge commit | `08260a397fb5b41d709b3c4074836cc7e21646da` (PR #3; its head `09386c2` had 7/7 CI jobs green before the merge) |
| P3B + P4 merge commit | `ab2d73d6e43f3c0122a08506ffedcd62c5ff19a8` (PR #4, head `c7aba9a`) |
| Development branch | `skillmirror-p5-student-experience` (P5, from `ab2d73d`) |
| Previous branch | `skillmirror-p3b-p4-evidence-mastery-debt` (merged in PR #4) |
| P3B + P4 commits (merged) | migrations 0005 + 0006 · P3B attribution + evidence qualification · P4 mastery + debt + ledger · pipeline stage + `GET /v1/ledger` · contracts · safety benchmark · ADR 0005 + acceptance script · evidence sources fix (`1352b5b`) · day-granular recency + this record |
| P2 + P3A branch started from `main` | `5ee62d01cb40ffac3dbf9342456935092a944098` |
| P2 + P3A commits (merged) | `508ec0b` migration 0003 · `76483c8` model_runs FK-null fix · `f160a32` ModelGateway + policy · `9550838` courses API + skill graph · `f6fb81b` P3A pipeline + worker · `41472e3` contracts · `a9d7720` web course flow · `07a4a10` benchmark smoke set · `c723960` ADR 0003 + CI + env · `2801ed4` format fix · `5ddd71c` provider-schema allowlist · `bb2448f` state + acceptance script · `20dbd77` live Gemini fixes (schema limits, quotas, overload) · `19bd72f` partial live acceptance record · then the ADR 0004 series: migration 0004 · free-tier ModelGateway + combined turn analysis · ADR 0004 + this record |
| CI (P3B + P4) | green on the PR #4 head before the merge |
| CI (P2 + P3A) | green on `2801ed4` ([36071071084](https://github.com/shaka9lakaboom/lappu1/actions/runs/36071071084)) and `bb2448f` ([36071473581](https://github.com/shaka9lakaboom/lappu1/actions/runs/36071473581)), 7/7 jobs each. The ADR 0004 series is checked in the pull request (not yet run when this record was written). |
| Pull request | P5 → `main`: opened after CI is green; **not merged** by the agent. P3B + P4: PR #4, **merged** as `ab2d73d`. P2 + P3A: PR #3, **merged** as `08260a3` |

## Phase

**Current phase: P5 — Complete Student Experience** (dashboard, Skill Map, Skill Detail with
"Why?", enriched Activity, corrections, deterministic recommendations). Branch
`skillmirror-p5-student-experience` from `main` `ab2d73d`. **Status: IN PROGRESS.** Hosted
migrations are **0001–0006**; P5 adds **0007** (feedback + recommendations), which is pushed to
hosted only after the owner's explicit approval. P6 verification will be **0008**. P5 makes no
model call (0 generation, 0 embedding requests).

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
- The free tier is unchanged: 20 generation requests per day and 5 per minute, per project and
  model, reset at midnight Pacific. No billing or paid tier was used.

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

- Latest migration: **`0006_mastery_debt.sql`** (ADR 0005).
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
- pgTAP: `0001` (13), `0002` (33), `0003` (49), `0004` (10), `0005` (40), `0006` (15) = **160**.
- **`0004_model_gateway_cache.sql` (ADR 0004)** is additive:
  - `model_runs.cache_key` and `model_runs.cache_source_run_id`, both nullable
  - two checks on those columns: a cache key only on `SUCCEEDED` rows; a cache hit is a
    first-attempt success
  - a cache lookup index and a provider-request (budget) index
- **Migration numbering:** `0004` is the free-tier ModelGateway/cache optimisation; P3B is
  `0005` and P4 is `0006`. **P5 (feedback + recommendations) is `0007`; P6 verification is
  `0008`.**
- Hosted: **`0001`–`0006` applied.**
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
| P3B / P4 versions | attribution `p3b-v1` (idempotency key), `attributor/p3b-v1`, qualifier `evidence/p3b-v1`, ledger `ledger/p4-v1` |
| Deterministic stages (no model call) | evidence strength = base × (0.75 + 0.5 d) × independence × min(mapping, attribution); mastery weighted Beta(1,1), recency half-life 180 d in whole UTC days, UNKNOWN below support 1.0; debt eligible at ≥ 2 recent (30 d) accepted high-confidence AI/SHARED delegations; `100 × pressure × gap × importance × confidence × 0.6` before P6; actionable ≥ 15 |
| Turn execution | `TURN_ANALYSIS_MODE=combined` (default): retrieval → 1 `TURN_ANALYSIS` call → gate → ≤ 1 adjudication; `staged` selectable |
| Routing policy | `architecture-default` (everything on `GEMINI_GENERATION_MODEL`); `free-tier` when `GEMINI_ROUTINE_MODEL` is set (routine turn tasks on it; graph and adjudication on the default model) |
| Request budget | `MODEL_DAILY_REQUEST_LIMITS=gemini-3.7-flash=20,gemini-3.8-flash=20`, `MODEL_QUOTA_RESERVE=2`, quota day midnight Pacific; deferral outcome `MODEL_BUDGET_RESERVE` |
| Result cache | exact key (provider, model, task, prompt version, input hash); memory + durable (`model_runs`) for structured output, memory for query vectors; hits logged with `cache_source_run_id` |
| Job types | `BOOTSTRAP_COURSE_GRAPH`, `PROCESS_RAW_MESSAGE` |

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
  for the run, with the reserve of 2, not a claim about Google's quota.

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
is deferred.

## URLs and versions (local)

| Item | Value |
| --- | --- |
| Web | http://localhost:3000 (`/courses`, `/courses/new`, `/courses/{id}` added) |
| API | http://localhost:8000: `/health`, `POST/GET /v1/events/…`, `POST /v1/courses`, `GET /v1/courses`, `GET /v1/courses/{id}`, `GET /v1/courses/{id}/skills`, **`GET /v1/ledger[?course_id=]`** (P4), OpenAPI `/docs` |
| Worker | in the API process when `DATABASE_URL` + `GEMINI_API_KEY` are set; or `python -m app.jobs.worker [--once]` |
| Deployed web / API | none, by decision (local-first) |
| Extension version | 0.2.0, dev id `cohpimnabjigooghbigblennedbplojm` (unchanged in this phase) |
| Backend version | 0.1.0 |

## Automated results (local run on 2026-09-25, Windows, Node 22.14, Python 3.13)

| Suite | Local | CI job |
| --- | --- | --- |
| Backend `ruff check` + `ruff format --check` (incl. benchmark runner) | clean | Backend |
| Backend pytest, unit (no DB) | **481 passed, 84 skipped** (P2+P3A: 307 / 71) | Backend |
| Backend pytest, with local Postgres | **565 passed** (85 `db`-marked; P2+P3A: 378 / 72) | Backend ingestion + intelligence + database |
| Database pgTAP (after `supabase db reset`) | **160 passed** (13 + 33 + 49 + 10 + 40 + 15) | Database |
| P3B/P4 safety benchmark (`benchmark/runners/p3b_p4_safety.py`, 22 deterministic cases) | **22/22**; False AI Assistance Debt Rate **0/21**; debt recall 2/2 | Backend (`test_benchmark_safety.py`) |
| Web ESLint | 0 problems | Web |
| Typecheck (web, contracts, config, ui, extension) | 5/5 clean | Web, Extension |
| Web vitest | **32 passed** | Web |
| Web production build (no env) | pass | Web |
| Extension vitest | **78 passed** | Extension |
| Extension build + manifest validation + Chromium (load ×2, capture → queue → sync ×1) | pass, **3 passed** | Extension |
| Real Gemini acceptance (P3B + P4) | **PASS on `gemini-3.5-flash-lite`**: 1 TURN_ANALYSIS + 1 SKILL_ATTRIBUTION + 1 embedding; replay 0 requests | not in CI by design |
| Real Gemini acceptance (P2 + P3A) | **PASS end to end on `gemini-3.5-flash-lite`** (3 generation requests; identical repeat 0); `gemini-3.7-flash` pending (503) | not in CI by design |
| P3A smoke benchmark (`benchmark/runners/p3a_smoke.py`, 12 cases) | live run **deferred**, per the owner (about 12–16 requests in combined mode) | schema + scorer only in CI |
| Real acceptance script (`services/backend/scripts/acceptance_p2_p3a.py`) | **passed** with `--resume-course`; prints the budget before and after; checks the identical repeat (0 provider requests) | not in CI by design |

New backend test modules (ADR 0005): `test_attribution`, `test_evidence_qualification`, `test_mastery`,
`test_debt`, `test_evidence_pipeline_db`, `test_ledger_api`, `test_benchmark_safety`. The P3A pipeline
tests run the P3A stage alone (`evidence=False`).
ADR 0004: `test_model_gateway_free_tier`, `test_turn_analysis`, `test_gateway_db`.
Earlier: `test_model_gateway` (27), `test_skill_graph_unit`, `test_retrieval_scoring`,
`test_qualification`, `test_mapping`, `test_courses_api`, `test_policy_db`, `test_skill_graph_db`,
`test_retrieval_db`, `test_pipeline_db`, `test_worker_db`, `test_contract_parity`, `test_benchmark_smoke`.

## CI

Existing jobs are extended; no job was added. P3B + P4:
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
- **Gemini free tier:** 20 generation requests/day and 5/minute per project and model. With ADR 0004 a normal turn needs 1 generation request (ambiguous: 2), and the smoke benchmark about 12–16 instead of about 36. Jobs defer instead of failing while over quota or at the budget reserve.
- **`turn-analysis/v1` is accepted live on `gemini-3.5-flash-lite`**, twice, at attempt 1 each. It has not been exercised on `gemini-3.7-flash` yet. If 3.7 ever rejects it, set `TURN_ANALYSIS_MODE=staged` (no code change).
- **`gemini-3.7-flash` live validation is pending.** On 2026-09-25, after the quota reset, 5 of 6 requests got HTTP 503 "high demand". Every 503 counts toward the local budget, so the worker was stopped instead of letting jobs cycle.
- **Flash-Lite graph size:** its graph has 24 skills, inside the 20–80 hard bounds but below the 30–60 target. The P1 turn "Explain binary search in one sentence." was rated low relevance (`STOP`). Both are quality observations for the P8 benchmark and calibration, not gate failures.
- **Combined mode retrieves once per unit.** A multi-task turn shares one top-20 pool between its segments, and a non-learning turn still costs one query embedding.
- **Query vectors are cached in memory only.** After a restart, an identical turn costs 1 embedding request; the generation still comes from the durable cache.
- **`gemini-3.5-flash-lite` quota is unknown.** It works live on this key, but its free-tier RPD/RPM is only visible in AI Studio or Cloud Quotas (ADR 0004 §7). Routing stays off by default, and the acceptance used a local cap of 10.
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
- `skill_candidates` has no review UI yet (P7). The course graph cannot be regenerated from the UI.
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
- Extension build (optional, public values): `SKILLMIRROR_SUPABASE_URL`, `SKILLMIRROR_SUPABASE_ANON_KEY`,
  `SKILLMIRROR_API_URL`, `SKILLMIRROR_WEB_URL`
- Tests: `TEST_DATABASE_URL`, `REQUIRE_DB_TESTS`, `UPDATE_GOLDEN`

## Exact next action

1. **P5 (in progress)** on `skillmirror-p5-student-experience`: migration `0007`, the student
   pages, corrections and deterministic recommendations. Migration 0007 goes to hosted only
   after the owner's explicit approval.
2. The P3B + P4 pull request was merged as PR #4 (`ab2d73d`).
3. **Optional, when quota allows** (Flash-Lite has 500 RPD):
   - one live turn in which the learner explicitly delegates a mapped skill (AI-actor
     evidence, single delegation → no debt): about 2 requests
   - the 12-case P3A smoke benchmark: about 12–16 requests
4. **`gemini-3.7-flash` live validation** stays pending until Google's capacity allows. The
   architecture default is unchanged.
