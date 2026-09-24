# SkillMirror — Live Project State

This record carries live implementation status (architecture §0.1). It must never
claim an unverified gate. Architecture: [`architecture/`](architecture/). Decisions:
[`decisions/`](decisions/) (0001 P0, 0002 P1).

**Last updated:** 2026-09-25

## Repository

| Field | Value |
| --- | --- |
| Repository | https://github.com/shaka9lakaboom/lappu1 |
| Default branch | `main` (P0 merged; P1 not yet merged) |
| P1 started from `main` | `b6ba8533cb63a780cefde7ccad7a943690216392` (merge of P0 PR #1) |
| Development branch | `skillmirror-p1-capture-ingestion` |
| P1 commits | `435dc85` db + contract · `d695406` backend ingestion · `cf75124` extension · `780abd4` web Activity + CI · `a1e19f7` ADR 0002 + live acceptance · `9defdbf` acceptance fix · closure commit adding this record |
| Earlier CI-verified P1 commit | `780abd4`, [run 36059437102](https://github.com/shaka9lakaboom/lappu1/actions/runs/36059437102), all 7 jobs green |
| HEAD | the P1 closure commit that adds this record; its CI run is linked from the P1 pull request |

## Phase

**Completed phase: P1 — Capture + ingestion. Status: COMPLETE.** The P1 exit gate
(architecture §19: "real ChatGPT turn captured, stored once, survives duplicate resend") passed
live against the hosted Supabase project on 2026-09-25. P0 remains green.

| Gate | State | Evidence |
| --- | --- | --- |
| P0 remains green | PASS | All P0 jobs/tests still pass (see *Automated results*) |
| Migration 0002 tested | PASS | pgTAP 33 assertions (46 total with 0001); applied to hosted |
| ChatGPTAdapter captures real visible messages | PASS | Live gate; real DOM fixtures |
| CaptureManager stable (one record per final message) | PASS | Unit + real-DOM transition tests; live gate |
| Pause/Resume | PASS | Unit tests; Chromium E2E |
| Queue survives interruption | PASS | Backend offline in the live gate; outage + browser restart in the Chromium E2E |
| Authenticated `POST /v1/events/batch` | PASS | Live gate (ES256 Supabase JWT via JWKS); backend tests |
| Learner identity from JWT only | PASS | Spoofed `learner_id` → 403 (live and tests) |
| Raw data stored durably | PASS | Hosted rows after Sync now |
| `processing_jobs` created | PASS | 2 `PROCESS_RAW_MESSAGE` jobs, `PENDING` |
| Duplicate resend → zero new canonical rows | PASS | Live: 2 resends → `accepted 0, duplicates 2`; counts unchanged |
| Activity page shows raw capture/status | PASS | Live: 2 rows, "Synced / Waiting for processing" |
| Real ChatGPT turn end-to-end | PASS | See *Live acceptance* |
| Complete local suite | PASS | See *Automated results* |
| CI green | PASS on `780abd4`; final SHA linked from the PR | GitHub Actions |

## Live acceptance (2026-09-25, `npm run acceptance:live --workspace @skillmirror/extension`)

Real ChatGPT (chatgpt.com, signed-out session) → unpacked extension `0.2.0`
(id `cohpimnabjigooghbigblennedbplojm`) → local backend (`uvicorn`, :8000) → hosted Supabase
Postgres (session pooler). One Playwright run, **1 passed** (1.1 min).

1. A fresh learner signed up on hosted Supabase Auth (`skillmirror-p1-1790286593990@mailinator.com`,
   id `47f93d92-e672-4b16-96ca-64bc1ec67aa9`). The learner signed in to the Companion popup.
2. With the backend **offline**, the prompt "Explain binary search in one sentence." was sent in real
   ChatGPT. Both messages were captured and **queued (2)**: the user message and ChatGPT's actual answer
   ("Binary search finds an item in a sorted list by repeatedly dividing the search range in half."),
   with ChatGPT's message ids and conversation id `6ab59b06-57e8-83ea-8e66-2b0d108cf94b`. The hosted
   row count was 0 at this point.
3. The backend started, **Sync now** was pressed, and the queue drained to 0.
4. Hosted Postgres (service-level count): **2 raw_messages, 1 conversation, 2 processing_jobs**. The
   learner read them through RLS with provenance intact: learner id from the JWT, `chatgpt` /
   `browser_extension`, external message ids, the assistant's parent = the user message,
   `message_index` 0/1, `revision_index` 0, `captured_at`/`received_at`, `client_event_uuid` = the
   queued `event_id`, and `capture_metadata` (extension 0.2.0, adapter chatgpt-1). Both jobs are `PENDING`.
5. The Activity page (web app → hosted, RLS) showed both rows.
6. The exact queued envelopes were resent twice: each resend returned `accepted 0, duplicates 2` with
   the stored ids. The counts were unchanged (2/1/2). A body with a spoofed `learner_id` returned **403**.

Evidence (local, gitignored): `apps/extension/test-results/acceptance/`, containing `evidence.json`,
`queued-envelopes.json`, `chatgpt.png`, `popup.png` and `activity.png`.

Test accounts: the passing learner above is kept as hosted evidence. The two learners from the
aborted runs (0 rows each) were deleted. No other `skillmirror-p1-*` accounts exist.

## Hosted Supabase verification (2026-09-25)

No project secrets, keys or project ref are recorded here.

- **Migrations:** `supabase migration list --linked` shows local `0001`/`0002` = remote `0001`/`0002`.
- **Catalog (read-only, `supabase db query --linked`):**
  - `conversations`, `raw_messages`, `attachments`, `processing_jobs`: RLS enabled.
  - The only policies are `*_select_own` (SELECT).
  - `authenticated` has SELECT only (no INSERT, UPDATE or DELETE). `anon` has no SELECT.
  - `activity_feed` is a view with `security_invoker=true`, SELECT for `authenticated` only.
- **Security advisors (`supabase db advisors --linked --type security`):** no findings.
- **JWT signing:** the project publishes ES256 keys (JWKS), so `SUPABASE_JWT_SECRET` is not needed.

## Database

- Latest migration: **`0002_capture_ingestion.sql`**. It adds enums, `conversations`, append-only
  `raw_messages` (update trigger blocks edits), `attachments` (metadata only), `processing_jobs`
  (§7.3 state machine, SKIP LOCKED claim primitives in `app/jobs/queue.py`) and the `activity_feed`
  view.
- Idempotency is enforced by unique indexes on the §6.6 preferred identity
  `(learner, provider, external_message_id, revision_index)`, on
  `(learner, provider, external_message_id, content_hash)`, and on the fallback
  `(learner, fingerprint)`.
- pgTAP: `supabase/tests/0001_p0_foundation.test.sql` (13) and `0002_capture_ingestion.test.sql` (33).

## URLs and versions

| Item | Value |
| --- | --- |
| Web (local) | http://localhost:3000 (`/activity` added) |
| API (local) | http://localhost:8000 (`/health`, `POST /v1/events/batch`, `GET /v1/events/sync-status`, OpenAPI at `/docs`) |
| Deployed web / API | none (deployment is P9) |
| Extension version | **0.2.0**, dev id `cohpimnabjigooghbigblennedbplojm` (from `manifest.json` `key`) |
| Extension permissions | `storage`, `alarms`; content script on `https://chatgpt.com/*` only; no host permissions |
| Backend version | 0.1.0 |

## Automated results (local run on 2026-09-25, Windows, Node 22.14, Python 3.13)

| Suite | Local | CI job |
| --- | --- | --- |
| Backend `ruff check` + `ruff format --check` | clean | Backend |
| Backend pytest, unit (no DB) | **97 passed, 20 skipped** | Backend |
| Backend pytest, with local Postgres (`TEST_DATABASE_URL`) | **117 passed** (21 `db`-marked) | Backend ingestion + database |
| Database pgTAP | **46 passed** (13 + 33) | Database |
| Web ESLint | 0 problems | Web |
| Typecheck (web, contracts, config, ui, extension) | 5/5 clean | Web, Extension |
| Web vitest | **24 passed** | Web |
| Extension vitest (adapter, capture, queue, sync, auth, contracts) | **78 passed** | Extension |
| Extension Chromium (load ×2, capture → queue → sync E2E ×1) | **3 passed** | Extension |
| Web + extension production builds | pass | Web, Extension |
| Live P1 acceptance (real ChatGPT + hosted) | **1 passed** | not in CI (needs a real browser session and hosted credentials) |
| Hosted auth round trip (P0) | not re-run in P1 | Auth round trip (local stack) |

## Known defects and caveats

- **Signed-in ChatGPT layout is covered only by a synthetic fixture.** The live gate used the signed-out
  ChatGPT shell (real DOM, recorded fixtures). The signed-in app layout (`[data-message-author-role]`)
  is supported, but it is tested against a fixture built from the publicly known structure. It has not
  been verified on a signed-in account. Record a real fixture and run the live gate signed in next.
- **DOM fragility:** a ChatGPT UI change can break selectors. The fixture tests fail loudly then, but the
  extension does not yet report "capture degraded" in the popup (§16 data-quality rule; planned for P8).
- **Opening an old conversation captures its visible messages once** (client and server deduplicate).
  This is not a bulk historical import, but it can record pre-install messages that the learner views.
- Attachments: metadata only. `context_incomplete=true` whenever attachment content was not captured.
- Auto-confirm is still on for the hosted project (see P0). Mailinator test addresses are public.
- During P1 closure, the hosted database password was entered in a malformed URL and fragments of it
  appeared in local tool output. It was then **reset by the owner**. The current password was never
  displayed.
- On the development machine, closing a Playwright Chromium browser is sometimes slow (see P0). The
  Chromium tests use long timeouts locally.
- No worker processes `processing_jobs` yet (P3). Jobs stay `PENDING` by design.

## Required environment variables (names only)

- Web (`apps/web/.env.local`): `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_API_URL`
- Backend (`services/backend/.env`): `APP_ENV`, `APP_NAME`, `API_VERSION`, `CORS_ORIGINS`
  (local: `http://localhost:3000,chrome-extension://cohpimnabjigooghbigblennedbplojm`), `SUPABASE_URL`,
  **`DATABASE_URL`** (session pooler URI, password percent-encoded), `SUPABASE_JWT_SECRET` (legacy
  HS256 only), `SUPABASE_ANON_KEY` / `SUPABASE_SERVICE_ROLE_KEY` (unused by P1)
- Extension build (optional, public values): `SKILLMIRROR_SUPABASE_URL`, `SKILLMIRROR_SUPABASE_ANON_KEY`,
  `SKILLMIRROR_API_URL`, `SKILLMIRROR_WEB_URL`. It falls back to the web `NEXT_PUBLIC_*` values.
- Tests: `TEST_DATABASE_URL`, `REQUIRE_DB_TESTS` (backend integration), `UPDATE_GOLDEN` (extension golden envelopes)

## Exact next action

Review and merge the P1 pull request `skillmirror-p1-capture-ingestion` → `main`. Optionally, first
run the live gate once with a **signed-in** ChatGPT account and record a real app-layout fixture. Then
start **P2 — Courses + Skill Graph** (architecture §19): course onboarding, the graph
generator/canonicalizer, skill tables (migration `0003`), embeddings and pgvector retrieval. The P2
exit gate: create an arbitrary course and retrieve relevant skill candidates.
