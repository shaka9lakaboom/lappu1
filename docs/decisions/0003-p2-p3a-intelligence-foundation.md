# 0003 — P2 courses + skill graph and P3A qualification, retrieval, mapping

- Status: accepted
- Date: 2026-09-25
- Scope: P2 (architecture §8, §9.3, §13) and the P3A part of P3 (§9.1–§9.4, §14, Appendix A/B).
  Attribution, EvidenceEvents, mastery, AI Assistance Debt and verification (P3B/P4+) are out of scope.
- Except for decision 1, none of these change the frozen architecture. They record how it was
  implemented where the architecture left a choice.

## Delivery

1. **SkillMirror is a local-first hackathon application.** The runtime is an unpacked Chrome
   extension, Next.js on `localhost:3000` and FastAPI on `localhost:8000`, backed by hosted
   Supabase and the Google AI API. There is no Vercel/Render deployment, production domain,
   cloud pipeline or production SMTP. **P9 is now "Local Demo Integration + Final Hardening"**,
   replacing "Deployment + release" (§15.1, §19). This supersedes the Chrome Web Store note in
   ADR 0002 §13: the extension stays unpacked.

## Model gateway (§14)

2. **Frozen model identifiers:** structured generation `gemini-3.7-flash`, embeddings
   `gemini-embedding-2` at 768 dimensions. Both are stable Gemini API models (checked against the
   Gemini API model pages on 2026-09-25). They can be overridden by `GEMINI_GENERATION_MODEL` /
   `GEMINI_EMBEDDING_MODEL`. The dimension is fixed by the schema (`vector(768)`). The thinking
   level defaults to `low`, because `gemini-3.7-flash` rejects `minimal`.
3. **Only `app/model_gateway/gemini.py` imports the SDK** (`google-genai` 2.25). No engine
   imports it. `gemini-embedding-2` takes the retrieval task as a text prefix
   (`task: search result | query: …`, `title: … | text: …`), not as `task_type`. Each text is sent
   as its own `Content`, because bare strings return one aggregated embedding.
4. **Strict output handling.** The gateway validates structured output with Pydantic strict mode:
   no extra fields, no coercion, plus a per-call semantic validator (for example, only candidate
   ids). Invalid output gets one repair call, which receives the rejected output and the errors.
   A second failure raises `ModelOutputInvalidError`, and the engine abstains.
5. **Every provider call writes one `model_runs` row.** It records task, provider, model,
   prompt_version, input hash, output hash, token usage, latency, status, attempt, repair link,
   trace id (`job:<id>`), learner, course and job. The row is written on its own connection, so it
   survives a job rollback. The validated output is stored; the prompt is not. `prompt_version`
   is mandatory, also for embeddings, where it versions the input format
   (`skill-embedding-text/v1`, `retrieval-query/v1`). `model_runs` is append-only, except for the
   `ON DELETE SET NULL` of learner and course, so deleting an account is never blocked.
6. **`GEMINI_API_KEY` is server-only.** Without the key, the gateway is absent and the worker
   does not start, so jobs stay `PENDING` instead of failing. CI never uses a key. Intelligence
   tests use a scripted fake provider (`services/backend/tests/fakes.py`). CI's secret scan also
   rejects Google API key patterns.

## Policy (§5.2, Appendix B)

7. **`policy_config` is the only source of tunable numbers.** Migration 0003 seeds these keys:
   `retrieval` (0.55/0.30/0.15, pool 20, rerank 8), `mapping` (0.80 / 0.65), `qualification`,
   `processing_unit` and `skill_graph`. `app/intelligence/policy.py` validates their shape but has
   **no defaults**; a missing or invalid key fails loudly. Every mapping decision stores the policy
   snapshot it used. Only global scope is read in P3A; course/skill scopes exist in the schema.

## Skill registry and course graph (§8)

8. **Canonical keys.** `skill_key()` folds case, punctuation, accents, `&`/`and`, simple plurals
   and British/American spellings (-isation, -ise, -yse, -elling). It keeps `C++`/`C#` distinct.
   The key is stored in `skill_nodes.normalized_name` and `skill_aliases.normalized_alias`. A
   normalized alias is **globally unique**, so a name resolves to exactly one UUID. A trigger
   registers the canonical name as the `CANONICAL` alias.
9. **Identity.** A trigger rejects any change to `skill_nodes.id`. A rename bumps `version` and
   demotes the old name to a `PREVIOUS_NAME` alias of the same UUID. A merge keeps the source UUID
   as `MERGED` → survivor, moves aliases (the old canonical name becomes `MERGED_NAME`) and moves
   course overlays. The old names keep resolving, to the survivor.
10. **Topics are registry nodes** (`TOPIC`), linked to skills by `PARENT` edges
    (from = parent). Only `SKILL`/`SUBSKILL` nodes are assessable, retrievable and mappable.
    `RELATED` edges are stored once (from < to). The canonicalizer refuses `PARENT` and
    `PREREQUISITE` edges that would close a cycle anywhere in the registry.
11. **Bootstrap vs NEW_SKILL_CANDIDATE.** The model never produces ids; it uses local keys
    (`s1`, `t1`). The canonicalizer is the controlled path that mints UUIDs. It reuses a node when
    the proposed name or all its aliases resolve to one existing skill. It attaches new aliases to
    the canonical UUID and refuses aliases owned by another skill. Only this canonicalizer creates
    `ACTIVE` nodes, during course bootstrap (§8.2). Unknown concepts met at mapping time go to
    `skill_candidates` (`PENDING_REVIEW`, deduplicated by key, counting occurrences). They are never
    registry nodes. Review and approval are P7.
12. **The bootstrap job is resumable.** Its stages are recorded in `courses.graph_status`
    (`PENDING → GENERATING → EMBEDDING → READY`, or `FAILED` after the job's 3 attempts). A retry
    after an embedding failure does not regenerate the graph, and a replay after `READY` is a no-op.
    The proposal must hold 20–80 skills (target 30–60). Duplicate or over-broad names, dangling
    references and cycles trigger the repair call.
13. **Courses.** `POST /v1/courses` writes the course, the creator's `STUDENT` membership (the
    creator is also `owner_id`) and the job in one transaction, and returns without any model
    call. An optional `Idempotency-Key` makes retries return the same course (200).
14. **Embeddings.** Each skill has one current row per (skill, model), with `graph_version` =
    node version and a `content_hash` of the exact embedded text (name + description + sorted
    aliases). Unchanged skills are never re-embedded; a rename or a new alias re-embeds that skill.
    There is an HNSW cosine index.

## Retrieval (§9.3)

15. **Two channels, two stages, exact rescoring.** Course scope first, global registry second.
    Each runs a full-text channel and a pgvector channel (up to 40 each). The union is rescored
    exactly: every pooled skill gets both channel scores.
    - lexical = `ts_rank_cd` of an OR-query of the text's English lexemes, divided by the best
      rank in the pool (relative score fusion)
    - semantic = cosine similarity
    - course prior = the skill's importance in the course scope, else 0
16. **Course scope.** The raw message's `active_course_id`, if the learner is a member; otherwise
    all the learner's active courses (at most 5). The raw-message → course FK is **not** added:
    `raw_messages` is append-only, and `ON DELETE SET NULL` would be an update. The id is
    validated at processing time instead.
17. **Rerank.** The top 20 are reranked by a structured call to the top 8. The reranker can only
    order ids it was given. Missing slots are filled in score order. If the pool already fits
    (≤ 8), no call is made. Invalid output twice falls back to score order
    (`rerank_fallback = true`).

## Qualification and mapping (§9.1–§9.4)

18. **Processing unit.** The unit is the user message plus the assistant reply it triggered, and
    up to 4 earlier messages (4 000 chars) as context only.
    - An assistant job analyses the pair.
    - A user job defers to the reply's job if a reply exists.
    - Without a reply, the user job re-queues every 20 s without spending attempts. After 120 s,
      the user message is analysed alone.
    - Orphan assistant messages and superseded revisions are skipped.
19. **Routing.** `high`/`medium` relevance counts as learning. Confidence below 0.60 is
    `UNCERTAIN`. Learning + not skill-bearing → `METADATA_ONLY`; non-learning → `STOP`.
    `context_incomplete` (or `MISSING_ATTACHMENT_CONTEXT`) → `UNCERTAIN`, never mapped. Invalid
    qualification output after the repair → one `UNCERTAIN` segment (`MODEL_OUTPUT_INVALID`)
    that keeps the unit text and provenance.
20. **Mapping.** The confidence gate:
    - ≥ 0.80: accept.
    - 0.65–0.79: one adjudication call for all band skills. `CONFIRM` at ≥ 0.80 is accepted,
      `REJECT` is rejected, anything else abstains.
    - < 0.65: abstain.

    If both a skill and its parent skill are accepted, the parent is rejected
    (`HIERARCHY_REDUNDANT`). Every proposed skill is stored with status, first-pass and final
    confidence, reason and span. A DB trigger rejects any `skill_mappings` row whose skill was not
    in that decision's mapper candidates, or is not an `ACTIVE` assessable skill.
21. **Persistence.** The P3A tables are `activity_segments`, `mapping_decisions` (retrieval pool
    with scores, reranked ids, all model-run ids, prompt versions, policy snapshot) and
    `skill_mappings`. All three are append-only and are written in one transaction per unit. They
    are keyed by (anchor message, `analysis_version` = `p3a-v1`, segment index) under an advisory
    lock, so replays and recovered jobs are no-ops. `processing_jobs.outcome` records the result
    (`MAPPED`, `ABSTAINED`, `NON_LEARNING`, `DEFERRED_TO_ASSISTANT`, …).

## Worker (§7.3)

22. **The worker runs in the API process.** It is a background thread, started when
    `DATABASE_URL` and `GEMINI_API_KEY` are set and `APP_ENV` is not `test`; it also runs as
    `python -m app.jobs.worker [--once]`. It claims jobs with `FOR UPDATE SKIP LOCKED` and retries
    with the P1 backoff. A `PROCESSING` lock older than 15 minutes is treated as a crashed worker:
    it counts as a failed attempt and the job is retried. A course whose bootstrap exhausts its
    attempts is marked `FAILED`.

## Web, contracts, tests

23. **Course pages call the backend server-side** with the learner's access token. They never
    mutate Supabase directly. `/courses` is protected by the proxy.
24. **Contracts.** TS contracts (`packages/contracts/src/courses.ts`, `intelligence.ts`) mirror
    the Pydantic models. `tests/test_contract_parity.py` checks that TS, Python and Postgres enums
    agree.
25. **Benchmark.** `benchmark/schema/` defines the case/label contract. `p3a-smoke` (12 cases)
    is scored by `benchmark/runners/p3a_smoke.py` against the live model. CI validates only the
    files and the scorer. The 120-case gate set remains P8.
26. **Lint.** Ruff's line-length rule is relaxed for `app/intelligence/**` (natural-language
    prompts) and `tests/**` (literal SQL).
