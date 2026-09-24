# SkillMirror — Live Project State

This record carries live implementation status (architecture §0.1). It must never
claim an unverified gate. Architecture: [`architecture/`](architecture/). Decisions:
[`decisions/`](decisions/) (0001 P0, 0002 P1, 0003 P2 + P3A).

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
| Commits | `508ec0b` migration 0003 · `76483c8` model_runs FK-null fix · `f160a32` ModelGateway + policy · `9550838` courses API + skill graph · `f6fb81b` P3A pipeline + worker · `41472e3` contracts · `a9d7720` web course flow · `07a4a10` benchmark smoke set · `c723960` ADR 0003 + CI + env · `2801ed4` format fix · `5ddd71c` provider-schema allowlist · `bb2448f` state + acceptance script · `20dbd77` live Gemini fixes (schema limits, quotas, overload) · closure commit adding this record |
| CI | green on `2801ed4` ([36071071084](https://github.com/shaka9lakaboom/lappu1/actions/runs/36071071084)) and `bb2448f` ([36071473581](https://github.com/shaka9lakaboom/lappu1/actions/runs/36071473581)), 7/7 jobs each; see *CI* for later commits |
| Pull request | **not opened**: the real end-to-end acceptance has not passed (see *Phase*) |

## Phase

**Current phase: P2 — Courses + Skill Graph and P3A — Qualification + Retrieval + Mapping.**
**Status: IMPLEMENTED; MIGRATION ON HOSTED; REAL END-TO-END ACCEPTANCE BLOCKED BY THE GEMINI
FREE-TIER DAILY QUOTA.**

Everything that runs without a real model passes locally and in CI, with a scripted fake provider.
Migration 0003 is applied to hosted Supabase and verified. The first live run against the real
Gemini API found three real defects; they were fixed and the fixes verified live (see
*Live acceptance*). The run then stopped at the provider's quota:

- The key is on the Gemini **free tier**: **20 generation requests per day per project per
  model** (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`) and 5 per minute.
- `gemini-3.7-flash` (the frozen default) was exhausted by diagnosing the first defect.
- `gemini-3.8-flash`, approved by the owner as a **temporary free-tier override for this
  acceptance only**, was then exhausted by schema verification.
- Daily quotas reset at midnight Pacific. No billing or paid tier was used.

**The remaining real gate** is the live chain: course graph generation → course skill embeddings
stored → retrieval over the real graph → a real turn through qualification → mapping or
abstention → provenance. It is ready to finish (see *Exact next action*). It does **not** count
as passed, on either model.

P3B (attribution, EvidenceEvents) and P4 (mastery, debt) are **not started**, by design.

| Gate | State | Evidence |
| --- | --- | --- |
| P0/P1 remain green | PASS | All P0/P1 tests pass locally; CI jobs unchanged apart from extensions |
| Migration 0003 applied/tested | PASS | pgTAP 49 assertions locally; applied to hosted 2026-09-25 (owner-approved), migration lists match, RLS/grants verified |
| Arbitrary course can be created | PASS (tests + **live on hosted**) | `test_courses_api.py`; live: fresh learner → `POST /v1/courses` → course + membership + bootstrap job, no model call in the request |
| Course skill graph generated | PASS with fake model; **real: BLOCKED (quota)** | `test_skill_graph_db.py`; live: the job was claimed and the real call made, then deferred on 429 (0 attempts spent); the graph schema itself was accepted live |
| Canonical registry works | PASS | Immutable UUID, rename → `PREVIOUS_NAME`, merge → `MERGED`, pgTAP + DB tests |
| Aliases / canonicalization | PASS | Variants (e.g. Visualization/Visualisation, plural, case) → one UUID; alias conflicts refused |
| Embeddings stored | PASS with fake vectors; real embedding call **PASS**; course embeddings stored **BLOCKED** | live `gemini-embedding-2`: two texts → two distinct 768-d vectors; a loop query ranks the loop skill above exceptions (0.776 vs 0.605) |
| Lexical + vector retrieval | PASS | `test_retrieval_db.py`: FTS incl. aliases, pgvector-only hit, course-first prior |
| Top 20 → top 8 | PASS | Pool of 20 ranked; reranker sees exactly the pool; 8 handed to the mapper |
| ModelGateway used by all model-dependent engines | PASS | Only `app/model_gateway/gemini.py` imports the SDK; engines call the gateway |
| model_runs logged | PASS (tests + **live**) | 15 live calls logged on hosted with task, model, prompt_version, status and error code (400 / 429 / 503) |
| Relevance / intent / skill-bearing | PASS with fake model; **real on a captured turn: BLOCKED (quota)** | `test_qualification.py`, `test_pipeline_db.py`; qualification schema accepted live |
| Segmentation | PASS with fake model | Mixed and multi-task turns → segments, only learning ones mapped |
| Mapping confidence gates | PASS | 0.80 accept, 0.65–0.79 adjudication, < 0.65 abstain (boundaries tested) |
| Unknown concepts do not silently become active skills | PASS | `skill_candidates` `PENDING_REVIEW`; skill count unchanged; DB guard on mappings |
| Abstention | PASS | Low confidence, unresolved adjudication, invalid output, missing attachment context |
| Provenance to raw activity | PASS | Segment → user/assistant raw ids; decision → pool, model runs, prompt versions, policy |
| Worker durable / idempotent | PASS | SKIP LOCKED (3 concurrent workers), retry, defer, crash recovery, idempotent rerun |
| Local + hosted acceptance | **BLOCKED (Gemini free-tier daily quota)** | Hosted and local parts pass; the live model chain awaits the quota reset |
| Complete local suite | PASS | See *Automated results* |
| CI green | PASS | 7/7 jobs on `2801ed4` and `bb2448f`; final commit: see *CI* |

## Database

- Latest migration: **`0003_courses_skill_graph.sql`**. It adds 13 tables, all with RLS enabled:
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
- pgTAP: `0001` (13), `0002` (33), `0003` (49) = **95**.
- Hosted: **`0001`–`0003` applied**. Migration 0003 was pushed on 2026-09-25 with the owner's
  approval, after these pre-checks:
  - the linked project ref equals the project in the backend `SUPABASE_URL`, the web
    `NEXT_PUBLIC_SUPABASE_URL` and `DATABASE_URL`
  - the dry run listed only `0003_courses_skill_graph.sql`
  - 0001/0002 are unchanged since `main`

  `supabase migration list --linked` shows local = remote for all three. Read-only catalog
  check:
  - 18/18 public tables have RLS enabled
  - `anon` has SELECT on none, and `authenticated` has no INSERT/UPDATE/DELETE
  - `model_runs`, `policy_config`, `skill_candidates`, `skill_embeddings` are server-only
  - the policy seed is present (weights 0.55/0.30/0.15)

  Security advisors: one WARN, *Leaked Password Protection Disabled*. This is an Auth project
  setting, not from the migrations.

## Intelligence configuration

| Item | Value |
| --- | --- |
| Generation model | `gemini-3.7-flash` (stable; structured JSON output; thinking level `low`) |
| Embedding model | `gemini-embedding-2`, 768 dimensions (task via text prefix; one `Content` per text) |
| SDK | `google-genai` 2.25 (only in `app/model_gateway/gemini.py`) |
| Prompt versions | `skill-graph-bootstrap/v1`, `relevance-intent/v1`, `skill-rerank/v1`, `skill-mapping/v1`, `mapping-adjudication/v1` |
| Embedding input versions | `skill-embedding-text/v1`, `retrieval-query/v1` |
| Analysis / mapper version | `p3a-v1` / `mapper/p3a-v1` |
| Job types | `BOOTSTRAP_COURSE_GRAPH`, `PROCESS_RAW_MESSAGE` |

## Live acceptance (2026-09-25): partial, stopped at the free-tier quota

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
| Backend pytest, unit (no DB) | **239 passed, 61 skipped** | Backend |
| Backend pytest, with local Postgres | **300 passed** (62 `db`-marked) | Backend ingestion + intelligence + database |
| Database pgTAP | **95 passed** (13 + 33 + 49) | Database |
| Web ESLint | 0 problems | Web |
| Typecheck (web, contracts, config, ui, extension) | 5/5 clean | Web, Extension |
| Web vitest | **32 passed** | Web |
| Web production build (no env) | pass | Web |
| Extension vitest | **78 passed** | Extension |
| Extension build + manifest validation + Chromium (load ×2, capture → queue → sync ×1) | pass, **3 passed** | Extension |
| Real Gemini acceptance (P2 + P3A) | **partial**: schemas, embeddings, course creation, model_runs, backpressure live; model chain **blocked by quota** | not in CI by design |
| P3A smoke benchmark (`benchmark/runners/p3a_smoke.py`, 12 cases) | live run **deferred** (≈36 calls > free-tier 20/day) | schema + scorer only in CI |
| Real acceptance script (`services/backend/scripts/acceptance_p2_p3a.py`) | ran up to the bootstrap wait; supports `--resume-course` | not in CI by design |

New backend test modules: `test_model_gateway` (27), `test_skill_graph_unit`, `test_retrieval_scoring`,
`test_qualification`, `test_mapping`, `test_courses_api`, `test_policy_db`, `test_skill_graph_db`,
`test_retrieval_db`, `test_pipeline_db`, `test_worker_db`, `test_contract_parity`, `test_benchmark_smoke`.

## CI

Existing jobs are extended; no job was added:
- The backend job lints `benchmark/runners`.
- Backend integration now covers courses, the skill graph, retrieval, the P3A pipeline and the worker.
- pgTAP includes 0003.
- The hygiene scan also rejects Google API keys.

No job needs `GEMINI_API_KEY`: intelligence tests use a scripted fake provider.
Run [36070884068](https://github.com/shaka9lakaboom/lappu1/actions/runs/36070884068) (`c723960`)
failed backend lint (one unformatted test file) and was cancelled by the fix. Run
[36071071084](https://github.com/shaka9lakaboom/lappu1/actions/runs/36071071084) (`2801ed4`)
passed all 7 jobs: hygiene, backend, backend integration, web, extension, database,
auth round trip.

## Known defects and caveats

- **Gemini free tier:** 20 generation requests/day and 5/minute per project and model. One acceptance needs about 5–7 more calls; the smoke benchmark needs about 36. Jobs defer instead of failing while over quota.
- **Real-model output quality is still unverified.** Prompts, schema compatibility with the Gemini JSON-schema
  subset, graph size and quality, and mapping calibration have only been exercised with a fake
  provider. The first real run may need prompt or schema adjustments (each would bump its
  `prompt_version`).
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
- Extension build (optional, public values): `SKILLMIRROR_SUPABASE_URL`, `SKILLMIRROR_SUPABASE_ANON_KEY`,
  `SKILLMIRROR_API_URL`, `SKILLMIRROR_WEB_URL`
- Tests: `TEST_DATABASE_URL`, `REQUIRE_DB_TESTS`, `UPDATE_GOLDEN`

## Exact next action

After the Gemini free quota resets (midnight Pacific):

1. Start the backend with the **default** model (no override): `npm run dev:backend`. For the free
   tier, add `GEMINI_GENERATION_RPM=4` to the process environment. The worker then finishes the
   pending work on hosted:
   - bootstrap of course `9440004a-a25e-4e15-94c0-17c21f6bd695` (1–2 calls + embeddings)
   - the P1 captured turn (1–3 calls)
2. Run
   `services/backend/.venv/Scripts/python services/backend/scripts/acceptance_p2_p3a.py --resume-course 9440004a-a25e-4e15-94c0-17c21f6bd695 --out test-results/p2-p3a-acceptance/evidence.json`.
   It verifies the graph (≈30–60 skills), stored 768-d embeddings, lexical + pgvector retrieval,
   then a real learning turn through qualification → top-20 → top-8 rerank → mapping/abstention,
   with provenance to `raw_messages` and `model_runs`. That is about 3–4 generation calls.
3. Check `/courses/9440004a-…` in the web app as the course owner. The UI check needs a learner
   whose password you know, or a fresh course created through `/courses/new`.
4. Record the evidence here. This live run on `gemini-3.7-flash` also closes the 3.7 validation
   item.
5. Then open the PR `skillmirror-p2-p3a-intelligence-foundation` → `main` (do not merge). Run the
   12-case smoke benchmark live when the quota allows. Then start P3B.
