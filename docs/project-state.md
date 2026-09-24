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
| Commits | `508ec0b` migration 0003 · `76483c8` model_runs FK-null fix · `f160a32` ModelGateway + policy · `9550838` courses API + skill graph · `f6fb81b` P3A pipeline + worker · `41472e3` contracts · `a9d7720` web course flow · `07a4a10` benchmark smoke set · `c723960` ADR 0003 + CI + env · `2801ed4` format fix · provider-schema allowlist + acceptance script · closure commit adding this record |
| CI | run [36071071084](https://github.com/shaka9lakaboom/lappu1/actions/runs/36071071084) on `2801ed4`: **all 7 jobs green**; later commits are linked from the PR |
| Pull request | not opened: the acceptance gate is blocked (see *Exact next action*) |

## Phase

**Current phase: P2 — Courses + Skill Graph and P3A — Qualification + Retrieval + Mapping.**
**Status: IMPLEMENTED, BLOCKED ON REAL ACCEPTANCE.** Everything that runs without a real
model passes locally and in CI (with a scripted fake model provider). Two gates still need a human:

1. **No `GEMINI_API_KEY` exists on this machine.** The real Gemini acceptance (course bootstrap,
   embeddings, retrieval and mapping on a real turn) has not run.
2. **Migration 0003 is not yet applied to the hosted project.** `supabase migration list --linked`
   shows remote `0001`/`0002` and local `0003` pending. `supabase db push --linked --dry-run`
   would push exactly `0003_courses_skill_graph.sql`. The real push was not executed: it needs the
   owner's explicit approval in this environment.

P3B (attribution, EvidenceEvents) and P4 (mastery, debt) are **not started**, by design.

| Gate | State | Evidence |
| --- | --- | --- |
| P0/P1 remain green | PASS | All P0/P1 tests pass locally; CI jobs unchanged apart from extensions |
| Migration 0003 applied/tested | PASS locally; **hosted: PENDING** | pgTAP 49 assertions on the local stack; hosted push awaits approval |
| Arbitrary course can be created | PASS (API + DB) | `test_courses_api.py`: 201, membership, job enqueued, no model call in request |
| Course skill graph generated | PASS with fake model; **real: BLOCKED** | `test_skill_graph_db.py` (30 skills, 4 topics, edges, overlay) |
| Canonical registry works | PASS | Immutable UUID, rename → `PREVIOUS_NAME`, merge → `MERGED`, pgTAP + DB tests |
| Aliases / canonicalization | PASS | Variants (e.g. Visualization/Visualisation, plural, case) → one UUID; alias conflicts refused |
| Embeddings stored | PASS with fake vectors; **real: BLOCKED** | 768-d `vector`, HNSW, content-hash cache, re-embed on rename/alias |
| Lexical + vector retrieval | PASS | `test_retrieval_db.py`: FTS incl. aliases, pgvector-only hit, course-first prior |
| Top 20 → top 8 | PASS | Pool of 20 ranked; reranker sees exactly the pool; 8 handed to the mapper |
| ModelGateway used by all model-dependent engines | PASS | Only `app/model_gateway/gemini.py` imports the SDK; engines call the gateway |
| model_runs logged | PASS | Every call: task, provider, model, prompt_version, input hash, tokens, latency, status, trace |
| Relevance / intent / skill-bearing | PASS with fake model; **real: BLOCKED** | `test_qualification.py`, `test_pipeline_db.py` |
| Segmentation | PASS with fake model | Mixed and multi-task turns → segments, only learning ones mapped |
| Mapping confidence gates | PASS | 0.80 accept, 0.65–0.79 adjudication, < 0.65 abstain (boundaries tested) |
| Unknown concepts do not silently become active skills | PASS | `skill_candidates` `PENDING_REVIEW`; skill count unchanged; DB guard on mappings |
| Abstention | PASS | Low confidence, unresolved adjudication, invalid output, missing attachment context |
| Provenance to raw activity | PASS | Segment → user/assistant raw ids; decision → pool, model runs, prompt versions, policy |
| Worker durable / idempotent | PASS | SKIP LOCKED (3 concurrent workers), retry, defer, crash recovery, idempotent rerun |
| Local + hosted acceptance | **BLOCKED** | Needs `GEMINI_API_KEY` and the hosted 0003 push (see above) |
| Complete local suite | PASS | See *Automated results* |
| CI green | PASS on `2801ed4` | Run 36071071084, 7/7 jobs (no Gemini key used) |

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
- Hosted: `0001`, `0002` applied; **`0003` pending** (see *Phase*).

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
| Backend pytest, unit (no DB) | **229 passed, 57 skipped** | Backend |
| Backend pytest, with local Postgres | **286 passed** (58 `db`-marked) | Backend ingestion + intelligence + database |
| Database pgTAP | **95 passed** (13 + 33 + 49) | Database |
| Web ESLint | 0 problems | Web |
| Typecheck (web, contracts, config, ui, extension) | 5/5 clean | Web, Extension |
| Web vitest | **32 passed** | Web |
| Web production build (no env) | pass | Web |
| Extension vitest | **78 passed** | Extension |
| Extension build + manifest validation + Chromium (load ×2, capture → queue → sync ×1) | pass, **3 passed** | Extension |
| Real Gemini acceptance (P2 + P3A) | **not run: no `GEMINI_API_KEY`** | not in CI by design |
| P3A smoke benchmark (`benchmark/runners/p3a_smoke.py`, 12 cases) | **not run: needs Gemini** | schema + scorer only in CI |
| Real acceptance script (`services/backend/scripts/acceptance_p2_p3a.py`) | ready; **not run: needs Gemini + hosted 0003** | not in CI by design |

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

- **Real-model behaviour is unverified.** Prompts, schema compatibility with the Gemini JSON-schema
  subset, graph size and quality, and mapping calibration have only been exercised with a fake
  provider. The first real run may need prompt or schema adjustments (each would bump its
  `prompt_version`).
- **Hosted database is one migration behind** (0003 pending) until the owner approves the push.
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
    `GEMINI_EMBEDDING_MODEL`, `GEMINI_THINKING_LEVEL`, `MODEL_TIMEOUT_SECONDS`, `WORKER_ENABLED`,
    `WORKER_POLL_SECONDS`, `WORKER_BATCH_SIZE`, `WORKER_STALE_AFTER_SECONDS`
- Extension build (optional, public values): `SKILLMIRROR_SUPABASE_URL`, `SKILLMIRROR_SUPABASE_ANON_KEY`,
  `SKILLMIRROR_API_URL`, `SKILLMIRROR_WEB_URL`
- Tests: `TEST_DATABASE_URL`, `REQUIRE_DB_TESTS`, `UPDATE_GOLDEN`

## Exact next action

1. Owner: put a Google AI key in `services/backend/.env` as `GEMINI_API_KEY=…`. Never commit or
   paste it anywhere else.
2. Owner: approve applying migration 0003 to the hosted project
   (`npx supabase db push --linked` from the repository root), or run it yourself.
3. Then run the real acceptance:
   1. Start the backend (`npm run dev:backend`, worker in-process) and the web app (`npm run dev:web`).
      `services/backend/scripts/acceptance_p2_p3a.py` automates steps 2–6 through the public APIs and
      writes an evidence JSON; the UI steps are then checked by hand.
   2. Create "Introduction to Python Programming" (Beginner) at `/courses/new`.
   3. Verify the bootstrap job completes: about 30–60 sensible skills, embeddings stored.
   4. Verify a retrieval query returns relevant candidates.
   5. Let the worker process an existing P1 raw turn, or a new simple learning turn.
   6. Verify qualification → retrieval → mapping or abstention, with provenance back to
      `raw_messages` and `model_runs`.
   7. Run `benchmark/runners/p3a_smoke.py` against that course.
4. Record the results here, then open the PR `skillmirror-p2-p3a-intelligence-foundation` → `main`
   (do not merge). Then start P3B (attribution + EvidenceEvents).
