# SkillMirror — Demo runbook (Windows first)

How to set up, start, present and clean up the SkillMirror hackathon demo from a fresh
checkout. Every command is PowerShell unless marked otherwise; run them from the repository
root. No secret value appears in this document, and none of the commands print one.

```
unpacked Chrome extension ──> Next.js web  http://localhost:3000
        │                           │
        └──────────────> FastAPI    http://localhost:8000  (+ in-process worker)
                                    │
                                    ├──> hosted Supabase  (Auth, Postgres + pgvector; migrations 0001–0009)
                                    └──> Google AI API    (Gemini; the demo uses gemini-3.5-flash-lite)
```

SkillMirror is **local-first**: there is no cloud deployment. The browser, the web app, the
API and the worker all run on the presenting laptop; only Supabase and Gemini are remote.

---

## 1. Prerequisites

| Tool | Version | Check |
| --- | --- | --- |
| Windows 10/11 | — | — |
| Git | any recent | `git --version` |
| Node.js | 22.12+ (see `.nvmrc`), npm 10+ | `node --version` |
| Python | 3.13 (3.12+ works) with the `py` launcher | `py -3.13 --version` |
| Google Chrome | current | — |
| Docker Desktop | only for local tests / the replay E2E, **not** for the demo | `docker ps` |

You also need:

- **A hosted Supabase project** with migrations `0001`–`0009` applied (see §4).
- **A Google AI (Gemini) API key** on the free tier (see §5).
- For cleanup scripts only: the project's **service-role key** (never in web or extension code).

## 2. Get the code and install

```powershell
git clone https://github.com/shaka9lakaboom/lappu1.git
cd lappu1
npm install
py -3.13 -m venv services\backend\.venv
services\backend\.venv\Scripts\python -m pip install -e "services/backend[dev]"
```

`npm run demo` finds `services\backend\.venv` automatically (or set `SKILLMIRROR_PYTHON`).

## 3. Environment files (names only; never commit them)

```powershell
Copy-Item services\backend\.env.example services\backend\.env
Copy-Item apps\web\.env.example apps\web\.env.local
Copy-Item apps\web\.env.e2e.example apps\web\.env.e2e.local   # cleanup scripts only
```

| File | Variables to fill |
| --- | --- |
| `services/backend/.env` | `APP_ENV=development`, `SUPABASE_URL`, `DATABASE_URL` (Supabase session pooler URI), `GEMINI_API_KEY`, `CORS_ORIGINS=http://localhost:3000,chrome-extension://cohpimnabjigooghbigblennedbplojm`; `SUPABASE_JWT_SECRET` only for legacy HS256 projects |
| `apps/web/.env.local` | `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` (anon / publishable key only), `NEXT_PUBLIC_API_URL=http://localhost:8000` |
| `apps/web/.env.e2e.local` | `SUPABASE_SERVICE_ROLE_KEY` — read only by test / cleanup scripts, never by the app |

Leave the model variables of the backend `.env` empty: `npm run demo` supplies the demo route.
The extension build reads the public values from `apps/web/.env.local`.

## 4. Supabase assumptions

- The hosted project has exactly migrations `0001`–`0009`. Check (read only):
  `npx supabase@2.117.0 migration list --linked` (after `npx supabase@2.117.0 link --project-ref <ref>`).
  **Never push a new migration without the owner's explicit approval** (the next one would be 0010).
- Email sign-ups are auto-confirmed (Auth "Confirm email" off) so demo accounts can sign in at once.
- Row-level security is on for every table; clients only read their own rows, all writes go
  through the API.

## 5. Gemini key and model budget

Create a key at https://aistudio.google.com/apikey (free tier; billing stays off) and put it in
`services/backend/.env` as `GEMINI_API_KEY`.

`npm run demo` runs the backend on the **hackathon operational runtime**: every generation task on
`gemini-3.5-flash-lite` (free tier 15 RPM / 500 requests per day), 12 requests/min, daily budget
`gemini-3.5-flash-lite=500`, reserve 25. The committed architecture default (`gemini-3.7-flash`)
is untouched in the code; it is simply not used by the demo. Typical costs:

| Action | Generation requests | Embedding requests |
| --- | --- | --- |
| One captured ChatGPT turn | 1 turn analysis + 1 per matched skill (attribution) | 1 |
| A new course (skill graph) | 1 | 1 |
| One verification challenge | 1 | 0 |
| Deterministic grading (MCQ / number) | 0 | 0 |

A 429/503 never fails a job: it is deferred and retried; nothing is lost.

## 6. Build and load the extension

```powershell
npm run build:extension
```

In Chrome: `chrome://extensions` → **Developer mode** on → **Load unpacked** → select
`apps\extension\dist`. The extension id is always `cohpimnabjigooghbigblennedbplojm` (fixed by
the manifest key, and allowed by `CORS_ORIGINS`). After every rebuild press **Reload** on its card.
The build writes `apps\extension\dist\build-info.json` (origins only) so the preflight can check it.

## 7. Start the demo

```powershell
npm run demo:check     # preflight; must end with "demo check: PASS"
npm run demo           # backend + worker (:8000) and web (:3000) in ONE terminal; Ctrl+C stops both
```

Wait for `[demo] backend ready in …s` and `[demo] web ready in …s`. Then, in a second terminal:

```powershell
npm run demo:verify    # read only; must end with "demo verify: PASS"
```

| Preflight refuses to start when … | What to do |
| --- | --- |
| port 8000 / 3000 is taken | stop the other server (see §12) — two backends = two competing workers |
| `services/backend/.env` or `apps/web/.env.local` is missing / incomplete | fill it (§3) |
| the extension is not built, is pre-P9, or points at a non-local API / web / another Supabase project | `npm run build:extension`, then reload it |
| the demo model route is invalid, or the backend rejects its configuration | the message names the field; fix `.env` |
| `APP_ENV=test` | set `APP_ENV=development` |

`demo:verify` checks, without writing anything: `/health`, the web app, the backend
configuration, that the worker is configured, the Flash-Lite route, the hosted database
(reachable; migrations equal to this checkout's 0001–0009, in a READ ONLY transaction), a
connected backend with no stuck job, and the extension build.

## 8. Sign in and use a course

1. Open http://localhost:3000 → **Sign up** (or sign in). New accounts are learners (STUDENT).
2. **Courses → New course**: name, subject, level. The skill graph appears in about a minute
   (1 generation + 1 embedding request). Or use an existing course.
3. The **Dashboard** answers: which course, what SkillMirror knows, what is still unknown (neutral:
   *Not enough activity yet* is never a low score), what needs attention, what to do next.
   Account ids and backend diagnostics are under **Settings & diagnostics** (dashboard footer).

## 9. Normal learner flow (live capture)

1. Click the SkillMirror Companion (puzzle icon → pin it) → **Sign in** with the same account →
   choose the **active course**.
2. Open https://chatgpt.com, start a **new** conversation (signed in), and ask something
   course-related, e.g. *"How do I loop over the items of a list in Python? Show me."*
3. The popup shows **Capturing (n messages visible)**; the turn syncs after the answer finishes.
   *ChatGPT layout not recognized* means the page structure changed (see §12).
4. **Activity** shows the turn within seconds of the answer: the matched skill, **Demonstrated by:
   You / AI / You + AI** (or *Explanation seen*), and what it means for mastery (independent evidence
   counts; AI assistance and exposure give no mastery credit). A general question can read
   *Low learning relevance — no skill evidence*; small talk reads *Not learning activity*.
5. Open the skill → **Why?**: gates, evidence timeline (each item: who, what, effect, spans,
   confidences, source link), the reliance signal (never a score headline) and the recommendation.

Existing conversations: opening an old conversation captures what is visible (history backfill).

## 10. Verification flow (the prepared VERIFY example)

A VERIFY recommendation needs repeated, confident AI delegation of an important skill, which
cannot be produced honestly in a few live minutes. Prepare a **disposable, clearly labelled demo
learner** instead (no model call; every seeded message starts `[SkillMirror demo data]`; the
recommendation engine itself issues VERIFY from that evidence):

```powershell
cd services\backend
.venv\Scripts\python scripts\demo_verify_fixture.py prepare     # prints the demo e-mail
.venv\Scripts\python scripts\demo_verify_fixture.py status      # before presenting: VERIFY active?
cd ..\..
Get-Content test-results\demo\demo-learner.password             # the demo password (git-ignored)
```

Prepare it on the demo day; `status` must show `('VERIFY', 'REPEATED_DELEGATION_UNVERIFIED')`.
Then, signed in as the demo learner:

1. **Dashboard → Needs your attention** lists the skill → **Start the check** (or open the
   **Verification Center**). Opening it plans the check (no model call); the worker writes ONE
   Flash-Lite challenge; the page refreshes until it is **Ready** (a few seconds).
2. **Open the check → Start the check** → answer (work without AI) → **Submit**.
3. Deterministic grading (MCQ / number: no model call) → **You passed this check** (or *Not yet*,
   which is one piece of evidence, never a verdict).
4. **See how this changed …**: the timeline has a *SkillMirror check* item; the state moves from
   *Not enough activity yet*; the reliance signal drops; the recommendation is no longer VERIFY.

`--debt-margin 0.3` prepares a thinner fixture on which one pass reaches **Verified** (prepare it
the same day). After the demo: `demo_verify_fixture.py cleanup` (see §13).

## 11. Teacher / admin roles

Roles come from the database only (never from token claims). The operator grants them:

```powershell
cd services\backend
.venv\Scripts\python scripts\grant_role.py --email someone@example.edu --role TEACHER   # or ADMIN
.venv\Scripts\python scripts\grant_role.py --email someone@example.edu --show
```

Each change is audited (`ROLE_CHANGE`). A teacher also needs a TEACHER membership of the course,
added by an admin (`POST /v1/admin/courses/{id}/members`). The teacher overview shows aggregates
only, and only for a cohort of at least 3 students (smaller groups show the cohort size only); no
student names, ids, AI usage or debt. **Admin** pages: overview, jobs (retry), model runs (no
prompts or outputs), skill candidates (review), benchmark (stored verdicts, with hard gates,
case completion and provider / transport failures shown separately).

## 12. Troubleshooting

| Symptom | Check / fix |
| --- | --- |
| `port 8000 is already in use` | `Get-NetTCPConnection -LocalPort 8000 -State Listen` → `Stop-Process -Id <OwningProcess>` |
| Activity shows *Waiting for processing* for long | `npm run demo:verify` (worker processing); backend log `job deferred: …` means 429/503 or the daily budget — it retries by itself |
| Popup: *Not signed in* / sync errors | sign in again in the popup; `CORS_ORIGINS` must contain the extension origin |
| Popup: *ChatGPT layout not recognized* | ChatGPT changed its page; record its structure (`apps/extension/scripts/record-chatgpt-structure.js`, instructions inside) and fix the adapter |
| Nothing captured in an open tab | reload the ChatGPT tab after (re)loading the extension |
| Course stuck *Generating* | quota / 503: it retries automatically; an admin can retry a FAILED bootstrap in **Admin → Jobs** |
| Sign-up says *Check your email* | turn off "Confirm email" in Supabase Auth (§4) |
| `demo:check` warns *extension sources changed* | `npm run build:extension`, then Reload in `chrome://extensions` |
| An old READY check the learner no longer needs | shown under *No longer needed* (history); it cannot be started and uses no daily budget |

## 13. Reset and cleanup

- Stop the demo: **Ctrl+C** in the `npm run demo` terminal (the worker stops cleanly).
- Remove the demo learner (Auth cascade; proves 0 learner-owned rows remain):
  `services\backend\.venv\Scripts\python services\backend\scripts\demo_verify_fixture.py cleanup`
- Local Supabase stack (tests only): `npx supabase@2.117.0 stop`.
- **Never re-run**: the P8 `recompute_skill.py --apply` for the remediated row, or the P9
  `sweep_p9_hosted.py run` (both are one-time, guarded, and already done on hosted).
- Nothing in the demo deletes captured evidence: evidence is immutable; a learner can only mark
  it *Don't count* / *Wrong skill* (one-way, audited).

## 14. Exact demo rehearsal flow (≈ 6–8 minutes)

| # | Step | What the audience sees | Expected wait |
| --- | --- | --- | --- |
| 1 | Dashboard (owner account) | course, what is known / unknown, attention, next steps | first page after a cold start: see §15 |
| 2 | Skill Map | topics, "n of m with a view", Not enough activity yet = neutral | — |
| 3 | ChatGPT: new conversation, one course question | the popup shows Capturing | the model's answer |
| 4 | Capture | the turn syncs when the answer completes | §15 |
| 5 | Activity | the skill chip: who demonstrated it (You / AI), effect on mastery | processing, §15 |
| 6 | Skill Detail → Why? | gates, timeline, spans, reliance signal explained | — |
| 7 | Sign out; sign in as the prepared demo learner | Dashboard: *Needs your attention* | — |
| 8 | Verification Center | the check is prepared | challenge generation, §15 |
| 9 | Generate one real Flash-Lite challenge | *Ready* | (same) |
| 10 | Answer it (without AI) | — | — |
| 11 | Deterministic grade | *You passed this check* | a few seconds |
| 12 | VERIFICATION evidence | *SkillMirror check* in the timeline | — |
| 13 | Mastery / reliance / recommendation update | state moves, reliance drops, VERIFY resolved | — |
| 14 | (optional) Admin → Benchmark | hard gates PASS, 71 / 72, 1 transport failure, stored FAIL | — |

Fallbacks: if ChatGPT or Gemini is slow, show the already-captured turns in Activity (their Why?
pages are complete); if the challenge is not ready within ~30 s, the page keeps refreshing — talk
through the Skill Detail meanwhile.

## 15. Measured timings (fresh Windows rehearsal, P9)

See `docs/project-state.md` → *Fresh Windows rehearsal P9* for the measured values of this
checkout (demo startup, first web page, ChatGPT sync, processing, verification generation) and the
steps flagged as likely to exceed ~30 s live.

## 16. Tests and CI (for maintainers)

```powershell
npm run test:backend        # pytest (DB tests need TEST_DATABASE_URL + the local stack)
npm run test:web; npm run test:extension:unit; npm run test:demo
npm run lint; npm run typecheck; npm run build
npx supabase@2.117.0 test db                         # pgTAP (local stack)
```

CI (`.github/workflows/ci.yml`, no job has a Gemini key): hygiene, backend (lint + unit), backend
integration (real Postgres: pipeline, worker, verification, critical gate deterministic 120/120 and
replay 72/72), web, extension (+ Chromium), database (pgTAP), auth round trip (local Supabase), and
**E2E replay**: the real API + worker + database + browser, driving sign-in → capture → Activity →
Skill Detail → verification → ledger on model output replayed from the committed recording.

## 17. Known limitations (honest list)

- **Architecture default not live-validated:** `gemini-3.7-flash` stays the committed default, but
  it was never validated live (503 "high demand" and a 20-request free quota); the 3.7 canary was
  skipped by the owner.
- **Flash-Lite is the hackathon operational runtime** (environment override in `npm run demo` only).
- **LIVE benchmark = 71/72, stored verdict FAIL:** REL-06 was stopped by a provider transport error;
  every zero-tolerance hard gate held. The stored row stays FAIL; it is never rewritten.
- **Verification graders:** MCQ / numeric / exact short answers are graded deterministically;
  free text by a rubric model; code / SQL executable graders are deferred (no sandbox).
- **Graph quality:** a model-generated graph can contain semantic near-duplicates (e.g. *Writing
  if-else conditional statements* next to the sub-skill *Writing if else statements*), an awkward
  topic (e.g. *Importing modules and packages* under *Error Handling and File I/O*) and imperfect
  prerequisite edges. Shared canonical skills are reused across courses by design.
- **Lexical grounding:** a learner span must be found in the learner's text; a paraphrase safely
  abstains (no evidence rather than wrong evidence).
- **History backfill:** opening an existing conversation captures its visible history.
- **Long / complex ChatGPT pages:** very long virtualized chats and complex reasoning layouts are
  not exhaustively proven; the signed-in structure is covered by a recorded structural fixture.
- **A check whose reason is gone** stays in the database as READY history (the lifecycle has no
  READY → closed transition); it is shown under *No longer needed* and becomes the current check
  again only if that skill is recommended for verification again.
- **Local-first runtime:** no cloud deployment, no production SMTP; the demo laptop runs everything.
