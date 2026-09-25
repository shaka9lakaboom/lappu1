# SkillMirror — Live Project State

This record carries live implementation status (architecture §0.1). It must never
claim an unverified gate. Architecture: [`architecture/`](architecture/). Decisions:
[`decisions/`](decisions/) (0001 P0, 0002 P1, 0003 P2 + P3A, 0004 free-tier ModelGateway).

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
| Default branch | `main`. P0 and P1 are merged. |
| P1 merge commit | `5ee62d01cb40ffac3dbf9342456935092a944098` (PR #2) |
| Development branch | `skillmirror-p2-p3a-intelligence-foundation` |
| Started from `main` | `5ee62d01cb40ffac3dbf9342456935092a944098` |
| Commits | `508ec0b` migration 0003 · `76483c8` model_runs FK-null fix · `f160a32` ModelGateway + policy · `9550838` courses API + skill graph · `f6fb81b` P3A pipeline + worker · `41472e3` contracts · `a9d7720` web course flow · `07a4a10` benchmark smoke set · `c723960` ADR 0003 + CI + env · `2801ed4` format fix · `5ddd71c` provider-schema allowlist · `bb2448f` state + acceptance script · `20dbd77` live Gemini fixes (schema limits, quotas, overload) · `19bd72f` partial live acceptance record · then the ADR 0004 series: migration 0004 · free-tier ModelGateway + combined turn analysis · ADR 0004 + this record |
| CI | green on `2801ed4` ([36071071084](https://github.com/shaka9lakaboom/lappu1/actions/runs/36071071084)) and `bb2448f` ([36071473581](https://github.com/shaka9lakaboom/lappu1/actions/runs/36071473581)), 7/7 jobs each. The ADR 0004 series is checked in the pull request (not yet run when this record was written). |
| Pull request | P2 + P3A → `main`, opened once CI on the final commit is green; **not merged** |

## Phase

**Current phase: P2 — Courses + Skill Graph and P3A — Qualification + Retrieval + Mapping.**
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

P3B (attribution, EvidenceEvents) and P4 (mastery, debt) are **not started**, by design. Their
migrations are **0005** (P3B) and **0006** (P4), because 0004 is now the ModelGateway cache.

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

- Latest migration: **`0004_model_gateway_cache.sql`** (ADR 0004). Before it, `0003_courses_skill_graph.sql`
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
- pgTAP: `0001` (13), `0002` (33), `0003` (49), `0004` (10) = **105**.
- **`0004_model_gateway_cache.sql` (ADR 0004)** is additive:
  - `model_runs.cache_key` and `model_runs.cache_source_run_id`, both nullable
  - two checks on those columns: a cache key only on `SUCCEEDED` rows; a cache hit is a
    first-attempt success
  - a cache lookup index and a provider-request (budget) index
- **Migration numbering (changed):** `0004` is the free-tier ModelGateway/cache optimisation.
  **P3B attribution/evidence must use `0005`**, and **P4 mastery/debt must use `0006`**.
- Hosted: **`0001`–`0004` applied.**
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
| Generation model | `gemini-3.7-flash`, the configured default (stable; structured JSON output; thinking level `low`). The live acceptance ran on `gemini-3.5-flash-lite` as a runtime override; the 3.7 live validation is pending (503) |
| Embedding model | `gemini-embedding-2`, 768 dimensions (task via text prefix; one `Content` per text) |
| SDK | `google-genai` 2.25 (only in `app/model_gateway/gemini.py`) |
| Prompt versions | `skill-graph-bootstrap/v1`, `turn-analysis/v1` + `turn-adjudication/v1` (combined, default), `relevance-intent/v1`, `skill-rerank/v1`, `skill-mapping/v1`, `mapping-adjudication/v1` (staged) |
| Embedding input versions | `skill-embedding-text/v1`, `retrieval-query/v1` |
| Analysis / mapper version | `p3a-v1` (both modes) / `mapper/p3a-turn-v1` (combined), `mapper/p3a-v1` (staged) |
| Turn execution | `TURN_ANALYSIS_MODE=combined` (default): retrieval → 1 `TURN_ANALYSIS` call → gate → ≤ 1 adjudication; `staged` selectable |
| Routing policy | `architecture-default` (everything on `GEMINI_GENERATION_MODEL`); `free-tier` when `GEMINI_ROUTINE_MODEL` is set (routine turn tasks on it; graph and adjudication on the default model) |
| Request budget | `MODEL_DAILY_REQUEST_LIMITS=gemini-3.7-flash=20,gemini-3.8-flash=20`, `MODEL_QUOTA_RESERVE=2`, quota day midnight Pacific; deferral outcome `MODEL_BUDGET_RESERVE` |
| Result cache | exact key (provider, model, task, prompt version, input hash); memory + durable (`model_runs`) for structured output, memory for query vectors; hits logged with `cache_source_run_id` |
| Job types | `BOOTSTRAP_COURSE_GRAPH`, `PROCESS_RAW_MESSAGE` |

## Live acceptance (2026-09-25, second run): PASS on `gemini-3.5-flash-lite`

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
| API | http://localhost:8000: `/health`, `POST/GET /v1/events/…`, `POST /v1/courses`, `GET /v1/courses`, `GET /v1/courses/{id}`, `GET /v1/courses/{id}/skills`, OpenAPI `/docs` |
| Worker | in the API process when `DATABASE_URL` + `GEMINI_API_KEY` are set; or `python -m app.jobs.worker [--once]` |
| Deployed web / API | none, by decision (local-first) |
| Extension version | 0.2.0, dev id `cohpimnabjigooghbigblennedbplojm` (unchanged in this phase) |
| Backend version | 0.1.0 |

## Automated results (local run on 2026-09-25, Windows, Node 22.14, Python 3.13)

| Suite | Local | CI job |
| --- | --- | --- |
| Backend `ruff check` + `ruff format --check` (incl. benchmark runner) | clean | Backend |
| Backend pytest, unit (no DB) | **307 passed, 71 skipped** (after ADR 0004; before: 239 / 61) | Backend |
| Backend pytest, with local Postgres | **378 passed** (72 `db`-marked; before: 300 / 62) | Backend ingestion + intelligence + database |
| Database pgTAP | **105 passed** (13 + 33 + 49 + 10) | Database |
| Web ESLint | 0 problems | Web |
| Typecheck (web, contracts, config, ui, extension) | 5/5 clean | Web, Extension |
| Web vitest | **32 passed** | Web |
| Web production build (no env) | pass | Web |
| Extension vitest | **78 passed** | Extension |
| Extension build + manifest validation + Chromium (load ×2, capture → queue → sync ×1) | pass, **3 passed** | Extension |
| Real Gemini acceptance (P2 + P3A) | **PASS end to end on `gemini-3.5-flash-lite`** (3 generation requests; identical repeat 0); `gemini-3.7-flash` pending (503) | not in CI by design |
| P3A smoke benchmark (`benchmark/runners/p3a_smoke.py`, 12 cases) | live run **deferred**, per the owner (about 12–16 requests in combined mode) | schema + scorer only in CI |
| Real acceptance script (`services/backend/scripts/acceptance_p2_p3a.py`) | **passed** with `--resume-course`; prints the budget before and after; checks the identical repeat (0 provider requests) | not in CI by design |

New backend test modules (ADR 0004): `test_model_gateway_free_tier`, `test_turn_analysis`, `test_gateway_db`.
Earlier: `test_model_gateway` (27), `test_skill_graph_unit`, `test_retrieval_scoring`,
`test_qualification`, `test_mapping`, `test_courses_api`, `test_policy_db`, `test_skill_graph_db`,
`test_retrieval_db`, `test_pipeline_db`, `test_worker_db`, `test_contract_parity`, `test_benchmark_smoke`.

## CI

Existing jobs are extended; no job was added:
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
- Extension build (optional, public values): `SKILLMIRROR_SUPABASE_URL`, `SKILLMIRROR_SUPABASE_ANON_KEY`,
  `SKILLMIRROR_API_URL`, `SKILLMIRROR_WEB_URL`
- Tests: `TEST_DATABASE_URL`, `REQUIRE_DB_TESTS`, `UPDATE_GOLDEN`

## Exact next action

1. **Pull request.** Review and merge the P2 + P3A pull request (`skillmirror-p2-p3a-intelligence-foundation`
   → `main`) once its CI is green. It is not merged by the agent.
2. **`gemini-3.7-flash` live validation**, when Google's capacity allows. Run it on a day with
   quota and no 503 spike: avoid the first hour after midnight Pacific.
   - **The pending work is done**, so create a fresh course through `/courses/new` or the script
     without `--resume-course`, then run the acceptance script with the default model: about 2
     generation requests plus embeddings.
   - **The cache is keyed by model**, so no 3.5-flash-lite output is reused for 3.7.
3. **Optional routing.** Read the `gemini-3.5-flash-lite` free-tier RPD in AI Studio. If it is
   materially higher than 20:
   - set `GEMINI_ROUTINE_MODEL=gemini-3.5-flash-lite`
   - add its limit to `MODEL_DAILY_REQUEST_LIMITS`
4. **The 12-case smoke benchmark** (live), when the quota allows: about 12–16 requests in combined
   mode.
5. **Then P3B** (attribution, EvidenceEvents), with **migration 0005**. P4 (mastery, debt) uses
   **0006**.
