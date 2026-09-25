# 0004 — Free-tier ModelGateway: one call per turn, exact result cache, request budget, routing

- Status: accepted. Verified live end to end on 2026-09-25 on `gemini-3.5-flash-lite` (runtime
  override). The `gemini-3.7-flash` live validation is pending: Google returned HTTP 503 "high
  demand" (see *Live validation*).
- Date: 2026-09-25
- Scope: how the ModelGateway and the P3A turn pipeline *execute*. All functional stages stay:
  segmentation, relevance, intent, skill-bearing, retrieval, top-20 → top-8 rerank, mapping,
  confidence gates, second-pass adjudication, abstention and provenance. The frozen model
  default (`gemini-3.7-flash`, ADR 0003 §2) and every `policy_config` number are unchanged.

## Context

The project's Gemini key is on the free tier: **20 generation requests per day and 5 per minute,
per project and per model**. The quota resets at midnight Pacific. Billing is not allowed.

The staged P3A pipeline (ADR 0003 §17–20) spends 3–5 generation requests per learning turn:

- qualification
- rerank
- mapping
- adjudication, when a mapping lands in the band
- repairs

A single day's quota covers four to six turns. The first live acceptance ran out of quota before
it could finish.

## Decisions

1. **Combined turn analysis is the default execution** (`TURN_ANALYSIS_MODE=combined`).
   - **Retrieval first, locally.** The unit's learner message and assistant reply build the
     query. Lexical + pgvector retrieval and the exact rescoring then build the top-20 pool:
     one query embedding and no generation request (`retrieve_pool`). A unit with uncaptured
     attachment context can never be mapped, so it skips retrieval.
   - **Then one structured call** (`TURN_ANALYSIS`, `turn-analysis/v1`). For each segment it
     returns:
     - the Appendix A.1 qualification fields
     - `ranked_candidate_ids`: at most 8, chosen only from the pool
     - the Appendix A.2 `mappings`: chosen only from that segment's top-8
     - `new_skill_candidate`
   - **The segment's top-8** is built in this order:
     1. the model's ranking, with duplicates removed and cut to 8
     2. ids the model mapped but did not rank (a mapping is an implicit rank)
     3. the rest of the pool, in candidate-score order
   - **Validation.** These trigger the single repair call:
     - an unknown id
     - a mapping outside the segment's top-8
     - a repeated mapping
     - too many segments or mappings
     - a bad new-skill parent

     If the repaired output is still invalid, the unit is kept as one `UNCERTAIN` segment
     (`MODEL_OUTPUT_INVALID`), exactly like a failed staged qualification. Two deviations are
     normalized instead of repaired, because they are harmless (like reason-code casing, ADR
     0003 §29): duplicate ranked ids, and more than 8 ranked ids.
   - **After the call, nothing changes.** Routing, the gate (0.80 / 0.65), the hierarchy rule,
     abstention and persistence are the staged code (`gate_proposals`, `apply_verdicts`,
     `settle_mapping`).
   - **Trade-off.** There is one pool per unit, not one per segment. A multi-task turn shares
     its 20 candidates between its segments. The staged path stays available
     (`TURN_ANALYSIS_MODE=staged`) as a fallback and for benchmark comparisons.
2. **Adjudication stays a separate, conditional call.** A turn has **at most one** adjudication
   request: `MAPPING_ADJUDICATION` with prompt `turn-adjudication/v1`. It covers every band
   proposal of the turn, across segments, keyed by item id. The verdict rules are unchanged. An
   invalid adjudication after the repair abstains (`ADJUDICATION_UNRESOLVED`).
3. **Provenance of a combined analysis.**
   - `activity_segments`: `prompt_version = turn-analysis/v1`, and
     `qualification_model_run_id` = the turn run.
   - `mapping_decisions`:
     - `rerank_model_run_id = mapping_model_run_id` = the turn run: the same call ranked and
       mapped.
     - `query_model_run_id` and `adjudication_model_run_id` as before.
     - `prompt_versions`: `{query_embedding, turn_analysis, adjudication}`
     - `mapper_version`: `mapper/p3a-turn-v1`

   `analysis_version` stays `p3a-v1` in both modes. It is the idempotency key, so switching
   modes never re-analyses a turn.
4. **Exact ModelGateway result cache** (`MODEL_RESULT_CACHE`, default on).
   - **Key.** The cache key covers provider, model, task type, prompt version and the input
     hash. The input hash covers system prompt, messages and response schema.
   - **What is cached.** Only validated outputs. A repaired output is cached under the
     *original* request's key. Failures, 429/503, timeouts and invalid outputs are never
     cached. Migration 0004 enforces this with a check: a cache key is allowed on
     `SUCCEEDED` rows only.
   - **Revalidation.** A cached output is validated again when served. If it fails the current
     validator, the gateway ignores it and calls the provider.
   - **Provenance of a hit.** A hit makes **zero provider requests**. It is still written as a
     `model_runs` row with `cache_source_run_id` pointing to the run that produced the output.
     Decisions reference the hit row, so each job's trace shows where its output came from.
   - **Tiers.**
     - structured outputs: an in-memory LRU, then a durable tier that reads `model_runs`
       itself (so it survives restarts)
     - query vectors: memory only
     - skill embeddings: their own durable content-hash cache (ADR 0003 §14), unchanged
     - graph bootstrap: stays resumable (§12)
   - **Across learners.** A hit can reuse another learner's run. Nothing leaks: the output is
     a function of the identical input alone, and `model_runs` is server-only.
5. **Quota-aware request budget** (`MODEL_DAILY_REQUEST_LIMITS`, `MODEL_QUOTA_RESERVE`,
   `MODEL_QUOTA_TIMEZONE`).
   - **Check before every provider request.** The gateway compares against the limit minus
     the reserve. It counts:
     - the model's requests since the quota day began (midnight Pacific), from `model_runs`
       rows that are not cache hits
     - plus the requests in flight in this process
   - **What counts.** Failed requests count too (on the safe side). Cache hits never count.
   - **When the budget is spent**, a transient `ModelBudgetExhaustedError` is raised *before*
     anything is sent. The worker defers the job (`MODEL_BUDGET_RESERVE`, at most 1 h, until
     the reset) without spending an attempt. 429/503 keep deferring as in ADR 0003 §28.
   - **Defaults.** The observed free-tier limits: `gemini-3.7-flash=20` and
     `gemini-3.8-flash=20`, with a **reserve of 2**. SkillMirror therefore never spends the
     last 2 requests of a model's day on its own. A model that is not listed is not budgeted.
     `off` disables the budget; a blank value keeps the default.
   - **RPM limiters** (`GEMINI_*_RPM`) are now per model, matching the per-model free-tier
     limits.
6. **Hackathon free-tier routing policy.** The default is unchanged: every generation task
   runs on `GEMINI_GENERATION_MODEL` (`gemini-3.7-flash`), the `architecture-default` policy.
   Setting `GEMINI_ROUTINE_MODEL` switches to the `free-tier` policy:
   - **Routine tasks** (one per turn) move to the routine model: `TURN_ANALYSIS`, plus
     staged-mode `RELEVANCE_CLASSIFICATION`, `SKILL_RERANK` and `SKILL_MAPPING`.
   - **High-value tasks** keep the default model and its separate quota:
     `SKILL_GRAPH_BOOTSTRAP` and `MAPPING_ADJUDICATION`.
   - `GEMINI_ROUTINE_THINKING_LEVEL` sets the routine model's thinking level (`minimal` is
     allowed).

   Model selection stays configuration only. A test checks that every engine task type is
   classified.
7. **Gemini 3.5 Flash-Lite, investigated without generation quota.** One `models.list`
   metadata request on the configured key showed that `models/gemini-3.5-flash-lite` is
   available to this project:
   - version `3.5-flash-lite-07-2026`
   - `generateContent` supported, thinking-capable
   - 1 048 576 input / 65 536 output tokens

   The Gemini API does not expose rate limits. Its free-tier RPD/RPM for this project is
   therefore **not assumed**, and routing is **not enabled by default**. To read the limits:
   - AI Studio (signed in as the key's owner, with the key's project selected): the usage /
     rate-limit page lists RPM, TPM and RPD per model for the project's tier.
   - Or the Google Cloud console → *APIs & Services → Generative Language API → Quotas*,
     filtered on `GenerateRequestsPerDayPerProjectPerModel-FreeTier` for model
     `gemini-3.5-flash-lite`.

   If that limit is materially higher than 20, set:
   - `GEMINI_ROUTINE_MODEL=gemini-3.5-flash-lite`
   - its limit in `MODEL_DAILY_REQUEST_LIMITS`, for example
     `gemini-3.7-flash=20,gemini-3.8-flash=20,gemini-3.5-flash-lite=<RPD>`
8. **Schema guard.** The worker (in the API process or standalone) and the acceptance script
   refuse to start on a database without migration 0004, because every `model_runs` insert
   would fail and burn job attempts. Jobs stay `PENDING`.
9. **Schema size, resolved on Flash-Lite.** The `turn-analysis/v1` response schema (1 746
   characters, 4 enums, no array bounds) is the largest yet. The real Gemini API accepted it on
   `gemini-3.5-flash-lite` at attempt 1 on both live turns. It has not been exercised on
   `gemini-3.7-flash` yet. If a model ever rejects it, `TURN_ANALYSIS_MODE=staged` returns to
   the older, live-accepted schemas without a code change.

## Expected provider requests

These are requests to Google, with an unchanged database state. A repair adds one request, and
only for invalid output.

| Work | Generation | Embedding |
| --- | --- | --- |
| Create a course: `POST /v1/courses` | 0 | 0 |
| Create a course: bootstrap job | 1 graph request (default model) | 1 for ≤ 32 skills, 2 for 33–60 |
| Normal captured learning turn: user-message job | 0 | 0 |
| Normal captured learning turn: assistant job | **1** (`TURN_ANALYSIS`) | 1 (query) |
| Ambiguous turn (a mapping in 0.65–0.79) | **2** (turn + one adjudication; still 2 with several band segments) | 1 |
| Non-learning turn | 1 | 1 (retrieval runs before the call) |
| Turn with uncaptured attachment context | 1 | 0 |
| Five normal learning turns | 5 (routine model under free-tier routing; 0 on `gemini-3.7-flash`) | 5 |
| Identical turn repeated (same unit and pool) | **0** | 0 in the same process; 1 after a restart |
| Job resumed after a 429 on its adjudication | 1 (the adjudication; the turn analysis comes from the cache) | 0 in the same process |
| Staged mode, normal / ambiguous turn (for comparison) | 3 / 4 | 1 |

## Live validation (2026-09-25)

Hosted Supabase (0001–0004) and the real Gemini API, on the pending course and the pending P1
turn. Full record: `docs/project-state.md` → *Live acceptance, second run*.

- **`gemini-3.7-flash` (configured default).** After the midnight-Pacific quota reset, 5 of 6
  requests returned HTTP 503 "This model is currently experiencing high demand"; the only success
  was a minimal quota check. Every job deferred with 0 attempts spent. The worker was stopped
  rather than let 503s drain the budget; every provider request counts toward it (§5).
- **`gemini-3.5-flash-lite` (runtime override for this acceptance only, not committed).**
  - Graph: 1 generation request, no repair; 24 skills; embedded with 1 embedding request.
  - Pending turn: 1 `TURN_ANALYSIS` request → `STOP`.
  - Acceptance turn: 1 `TURN_ANALYSIS` request → pool 20 → top-8 → `MAPPED` (two skills at
    0.90 and 0.80, no adjudication).
  - The identical repeat was served entirely from the cache: **0 provider requests**, and both
    hit rows point to their source runs.
  - Reserve intact.
- **Not seen live:** an adjudication (no mapping landed in 0.65–0.79). That path is covered by
  the tests.
