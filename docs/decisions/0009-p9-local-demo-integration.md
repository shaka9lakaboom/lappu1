# 0009 — P9 local demo integration + final hardening

- Status: accepted; implemented on branch `skillmirror-p9-final-demo-hardening` (from `main`
  `01e828c`). **No migration** (hosted stays at 0001–0009). The one hosted maintenance write (§1)
  and the disposable demo / smoke accounts (§11, §13) follow the brief's explicit authorisation and
  the owner's standing rules (disposable accounts, existing canonical skills, the real P3B/P4 learner
  untouched). Live results: `docs/project-state.md`, *P9*.
- Date: 2026-09-26
- Scope: the final hackathon phase — local demo integration, learner-facing UX polish, fresh-start
  reliability, the replay end-to-end CI job (H13), the recorded signed-in ChatGPT fixture (H10), the
  benchmark presentation, the demo preflight / verify, and the handoff runbook
  (`docs/demo-runbook.md`). No new architecture; no deployment.

## 1. The controlled first P8 startup sweep

The first worker of the merged P8 code re-derives every ledger row computed before today's UTC
midnight or by an older ledger algorithm (H7). On hosted that meant re-tagging the rows of the two
existing learners and inserting NO_ACTION recommendations for the real P3B/P4 learner — expected
derived changes, but on real learners' rows. It was run **once, under control**, before any demo
worker started: `services/backend/scripts/sweep_p9_hosted.py`

- `snapshot` (READ ONLY: whole-database hashes, per learner provenance hashes and the full ledger /
  recommendation rows) → `preview` (the sweep in a transaction that is rolled back) → `run` (once;
  refused a second time or if the database moved since the snapshot; `sweep_stale_ledgers`, the
  worker's own function, with no model gateway in the process) → `verify` (READ ONLY).
- Result (2026-09-25 20:54 UTC): verify 10/10. Only `skill_ledger` and `recommendations` changed;
  provenance (raw messages, conversations, segments, decisions, mappings, attributions, evidence,
  feedback, verification sessions / results, jobs) identical for both learners; 0 model runs; every
  row on `ledger/p8-v1` (values unchanged; `ledger_version` +1 for the 4 re-tagged rows); the
  remediated P8 row untouched (not stale); 24 NO_ACTION recommendations for `97d8e181…`; no VERIFY;
  READY `3aa4481e…` and the migrations unchanged. Later daily sweeps are normal product behaviour.

## 2. A check whose reason is gone (stale READY) — skill-scoped current check

The hosted READY session `3aa4481e…` was planned from VERIFY `e8d6092f…`, which the P8 remediation
superseded (the debt behind it was false). The brief's literal rule — *actionable READY = READY AND
its originating recommendation still ACTIVE AND of type VERIFY / REVERIFY* — was analysed before it
was implemented and **deadlocks**:

- the planner's `blocking_reason` treats any READY session as `ACTIVE_SESSION`;
- the 0008 unique index `verification_sessions_one_active_key` includes READY (one open session per
  learner and skill);
- the 0008 lifecycle guard allows only READY → IN_PROGRESS (no READY → ABANDONED).

A genuinely new VERIFY of that skill (a new recommendation id) would then be neither plannable nor
visible. This was reported to the owner, who chose the **skill-scoped rule** (no migration, no
lifecycle change):

> A **current check** is a session whose skill has an ACTIVE VERIFY / REVERIFY recommendation. A
> session the learner never started (PLANNED / READY, no failure) without it is **NOT_NEEDED**.

- NOT_NEEDED is a derived status (`status_of`); the stored state stays READY. It is listed in a new
  `not_needed` group (collapsed *No longer needed* history), never under *Ready for you*, never
  linked from a recommendation, and `POST /start` refuses it (409). It is not counted in today's
  budget (planner `verification-planner/p9-v1`). Nothing is written to it.
- If the skill is recommended for verification again, the same session is the current check again —
  consistent with `list_recommendations`, which already links a VERIFY / REVERIFY to the skill's open
  session by skill. IN_PROGRESS stays resumable whatever happened to the recommendation; EVALUATED is
  history.
- Hosted, verified read-only (`list_verifications` in a rolled-back transaction): `3aa4481e…` is
  NOT_NEEDED, no ready check, budget untouched, nothing persisted.
- Regression: READY + ACTIVE VERIFY → actionable; READY + SUPERSEDED VERIFY → history (not started,
  not counted, 409 on start, row unchanged); re-adoption by a new VERIFY (no deadlock); IN_PROGRESS →
  resumable → EVALUATED → history (`test_verification_db.py`, `test_verification_status.py`, web).

## 3. The learner dashboard

The dashboard answers five questions: which course; what SkillMirror knows (skills with a formed
view); what is still unknown (UNKNOWN, neutral: *Not enough activity yet* is never a low score); what
needs attention (current VERIFY / REVERIFY checks only); what to do next (practice, prerequisites).
The raw user id, profile timestamps and the backend version / health card moved to a protected
**Settings & diagnostics** page (`/settings`). The CI auth round trip reads the account record there
and asserts the dashboard shows none of it.

## 4. Activity and evidence wording

Every learner-facing evidence label follows the **recorded** evidence (type and exclusion after the
deterministic qualification), never the attributor's claim or the raw model output:

| Recorded type | Who demonstrated this skill? | Effect |
| --- | --- | --- |
| independent explanation / application / transfer, checks, teacher evidence | You | counts toward mastery |
| assisted attempt | You + AI | counts with reduced weight |
| observation | AI | AI-assistance evidence: no mastery credit |
| exposure | *Explanation seen* | does not affect mastery |

The "who" is derived from the type, so a chip can never read "You" next to "The AI did it". A turn is
summarised by its strongest counted evidence (*Independent learner evidence recorded*, *AI-assistance
evidence recorded — no mastery credit*, *Exposure only — does not affect mastery*); a bare "Evidence
recorded" is never shown. The reply row reads the turn's units from its anchor message.

## 5. Learning-related but no skill evidence

The processing contract distinguishes three cases; the labels follow the recorded classification:

- `METADATA_ONLY` (learning relevance high / medium, not skill-bearing) → *Learning activity — no
  skill evidence*;
- `STOP` with learning relevance **low** → *Low learning relevance — no skill evidence* (STOP covers
  both "low" and "none" because the policy's learning levels are high / medium);
- `STOP` with relevance **none** → *Not learning activity* (unchanged).

The hosted "what is python" unit is recorded as academic / learn / **low** (0.9) → STOP, not as
METADATA_ONLY; it now reads *Low learning relevance — no skill evidence*. The classification itself
is unchanged.

## 6. Skill map

Presentation only (no regeneration, the canonical graph untouched): each topic card says how many of
its skills SkillMirror has a view on, and sub-skills are indented and marked. Checked read-only on
hosted: every skill of both course graphs groups under one of its own course's topics. Observed
quality limits are documented (§14), not patched.

## 7. H10: the recorded signed-in ChatGPT session

`apps/extension/scripts/record-chatgpt-structure.js` is a DevTools snippet that records the open
signed-in conversation as **structure only**: every word becomes a synthetic word (keyed hash with a
random per-recording salt that is never written out — unchanged text stays identical across frames
and cannot be reversed), every UUID a sequential pseudonym, file names `file-N.<ext>`; `href` /
`src` / `alt`, scripts, styles, media and comments are dropped; aria-labels are kept only for fixed
control labels; no cookie, storage, network or page state is read (a source test enforces it).
`start()` / `stop()` record a timed frame sequence.

`tests/fixtures/chatgpt/signed-in/recording.json` is a session recorded frame by frame through that
recorder from the structure-from-live fixture (fixed salt; `npm run fixture:signed-in --workspace
@skillmirror/extension` rebuilds it byte-identically): user turn, assistant streaming, complete,
regenerated, edited user message, attachment, math streaming / complete, degraded layout. The replay
test drives the unchanged `chatgpt-2` adapter + `CaptureManager` over the frames. A live recording by
the owner (`recording-live.json`) replays through the same test once added.

## 8. H13: the replay end-to-end CI job

`E2E replay` (`.github/workflows/ci.yml`): a local Supabase stack (Auth + Postgres), the real FastAPI
app (`create_app`, every `/v1` route) and the real durable worker (`build_worker`) on a gateway whose
provider **replays** the committed Flash-Lite recording; the one task whose prompt carries a fresh id
(verification generation: the new session id) gets a deterministic challenge. No job has a Gemini
key; a request missing from the recording fails as STALE_RECORDING. The flow is the critical gate's
DEBT-18, sent as the extension sends it: sign in → course → three delegation turns processed by the
worker → Activity (*Demonstrated by: AI*, no mastery credit) → Skill Detail (UNKNOWN, reliance signal,
Why?) → VERIFY → Verification Center plans, the worker prepares → answer → deterministic grade →
VERIFICATION evidence → ledger re-derived (recently passed, debt < 15) → VERIFY completed.
`services/backend/scripts/e2e_replay.py verify` then checks the database (every model run the replay
provider, 0 misses, 6 messages, 3 delegations, one passed verification with its evidence, the ledger
and recommendation update). ~3 minutes in CI.

## 9. Benchmark presentation

A stored verdict mixes two questions. `/admin/benchmark` keeps each stored verdict exactly as
recorded and shows its parts next to it: safety / correctness hard gates, case completion, and
provider / transport failures (`app/admin/benchmark_view.py`). In LIVE / REPLAY a case fails only on a
hard gate or an execution error, so a failed case in a family without hard failures did not complete.
The hosted LIVE row reads: hard gates PASS (13/13), 71 / 72 cases passed, provider / transport
failures 1 (REL-06), **stored verdict FAIL**, with a note that a live run passes only when every case
completes. The row kept only the failing id; REL-06's cause (`ModelUnavailableError(TRANSPORT)`) comes
from the saved runner report, named on the page with its sha256. A real hard-gate failure is never
softened. From P9 on, the runner stores each failing case's error as a code only (`report.errors`).

## 10. Demo preflight and verify

`npm run demo:check` (and `npm run demo`) refuse to start when a port is taken, an env file is missing
or incomplete, the extension is not built / pre-P9 / built for a non-local API or web or another
Supabase project (the build writes `dist/build-info.json`: origins only), or the demo route is invalid
— validated in JS (Flash-Lite budgeted, inside its 15 RPM / 500 RPD free tier, reserve below the
limit, worker on) and by the backend's own `Settings` with the demo overrides
(`scripts/demo_probe.py config`: field and message only, never a value). `npm run demo:verify` (read
only) checks `/health`, the web app, the configuration, the worker, the Flash-Lite route, the hosted
database and its migrations (equal to the checkout's, READ ONLY), job liveness and the extension build.

*Finding (fresh rehearsal):* through the Supabase pooler every client appears as `Supavisor` in
`pg_stat_activity`, so a connection count cannot prove the worker is alive; liveness is judged from
jobs instead (none waiting > 3 minutes, no stale lock).

## 11. The prepared VERIFY example

`services/backend/scripts/demo_verify_fixture.py` prepares one disposable, clearly labelled demo
learner (display name *SkillMirror demo learner*; every seeded message starts `[SkillMirror demo
data]`) on the existing course, seeding learner-owned captured turns through the production
qualification, ledger and recommendation code (no model call). Repeated confident AI delegation of one
canonical course skill makes the debt actionable, so **the recommendation engine itself** issues
VERIFY; nothing inserts a recommendation, session or item; no registry row is created. Sizing reuses
the owner-approved P6 fixture search with a debt margin (default 2.0) so recency decay cannot drop the
VERIFY before the demo (hosted: *Writing if-else conditional statements*, debt 25.8; one pass drops
it below actionable). `--debt-margin 0.3` gives the thin fixture on which one pass reaches VERIFIED.
`cleanup` deletes the learner through the Auth admin API and proves 0 learner-owned rows.

## 12. Real model budget

P9 target ≤ 5 generation and ≤ 3 embedding requests; no live benchmark, no 3.7 canary. Used: see
`docs/project-state.md` (*P9: real model calls*).

## 13. Validation

See `docs/project-state.md` (*P9*): the local suites, CI (8 jobs), the local P7 acceptance on the P9
code, the hosted smoke and the fresh Windows rehearsal with its timings.

## 14. Known limitations

- `gemini-3.7-flash` stays the committed architecture default and was never validated live (503
  "high demand", 20 requests / day); the 3.7 canary was skipped by the owner.
- `gemini-3.5-flash-lite` is the hackathon operational runtime (environment override only).
- LIVE benchmark 71/72, stored verdict FAIL: REL-06 was stopped by a provider transport error; every
  zero-tolerance hard gate held. The row is never rewritten.
- Code / SQL executable verification graders are deferred (no sandbox).
- Graph quality: semantic near-duplicates (e.g. *Writing if-else conditional statements* and the
  sub-skill *Writing if else statements* in one course), an awkward topic (*Importing modules and
  packages* under *Error Handling and File I/O*), imperfect prerequisite edges. Calibration is
  post-hackathon.
- Lexical grounding may safely abstain on a paraphrased learner span.
- Opening an existing conversation backfills its visible history.
- Very long virtualized chats and complex reasoning layouts are not exhaustively proven.
- A NOT_NEEDED session stays READY in the database (no READY → closed transition without a
  migration); its skill cannot get a second open session, by design (it becomes the current check).
- A PLANNED session whose VERIFY is superseded before generation still spends its one generation.
- Local-first runtime: no cloud deployment.
