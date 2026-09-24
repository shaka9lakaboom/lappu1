# SkillMirror

**Use AI. Keep the skill.** SkillMirror observes AI-assisted work, separates what a
learner demonstrated from what they delegated, and verifies skills that still lack
independent evidence.

- Architecture (frozen V1 source of truth): [`docs/architecture/`](docs/architecture/)
- Live implementation status: [`docs/project-state.md`](docs/project-state.md)
- Implementation decisions: [`docs/decisions/`](docs/decisions/)

Current phase: **P2 — Courses + Skill Graph and P3A — Qualification + Retrieval + Mapping**
(see [`docs/project-state.md`](docs/project-state.md) for the verified status). Attribution,
evidence, mastery and verification are later phases.

SkillMirror is a **local-first hackathon application**: an unpacked Chrome extension, the
Next.js web app on `localhost:3000` and the FastAPI backend on `localhost:8000`, backed by a
hosted Supabase project and the Google AI (Gemini) API. There is no Vercel/Render deployment.

## Repository layout

```
apps/
  web/            Next.js 16 + TypeScript + Tailwind 4 + shadcn/ui-compatible, Supabase Auth
  extension/      Chrome Manifest V3 companion (TypeScript, bundled with esbuild)
services/
  backend/        FastAPI service; engine boundaries are modules inside one deployable
packages/
  contracts/      Canonical enums, TS types and JSON schemas (ingestion, courses, intelligence)
  config/         Shared constants (tunable policy lives in the policy_config table)
  ui/             Shared UI utilities
supabase/
  migrations/     Numbered SQL migrations (0001 foundation, 0002 capture, 0003 courses + skill graph)
  seed/           Local seed (intentionally empty: no fake data)
  tests/          pgTAP tests for migrations and RLS
benchmark/        Intelligence benchmark schema, cases, labels, runners (P3A smoke set)
docs/             Architecture, decisions, project state
.github/workflows/ci.yml
```

## Prerequisites

- Node.js 22.12+ (see `.nvmrc`) and npm 10+
- Python 3.12+
- A Supabase project (hosted), or the Supabase CLI + Docker for a local stack

## Install

```bash
npm install

cd services/backend
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1      macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## Configure

Environment files are never committed. Copy the templates and fill in values from
**Supabase → Project Settings → API**:

```bash
cp apps/web/.env.example apps/web/.env.local
cp services/backend/.env.example services/backend/.env
```

| Variable | Where | Notes |
| --- | --- | --- |
| `NEXT_PUBLIC_SUPABASE_URL` | web | Project URL. Required; the app refuses to start without it. |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | web | Anon or publishable key. A secret/service-role key is rejected. |
| `NEXT_PUBLIC_API_URL` | web | Backend base URL, e.g. `http://localhost:8000`. Needed for the Courses pages. |
| `APP_ENV` | backend | `development` \| `test` \| `staging` \| `production` |
| `APP_NAME`, `API_VERSION` | backend | Reported by `/health`; sensible defaults. |
| `CORS_ORIGINS` | backend | Comma-separated exact origins, e.g. `http://localhost:3000` |
| `SUPABASE_URL` | backend | Needed to verify access tokens. Required in staging/production. |
| `SUPABASE_JWT_SECRET` | backend | Only for projects on the legacy HS256 JWT secret. |
| `DATABASE_URL` | backend | Postgres connection string (Supabase session pooler). Server only. |
| `GEMINI_API_KEY` | backend | Google AI key. Server only. Without it the worker stays idle and jobs stay `PENDING`. |
| `GEMINI_GENERATION_MODEL`, `GEMINI_EMBEDDING_MODEL` | backend | Defaults `gemini-3.7-flash`, `gemini-embedding-2` (768 dims). |
| `WORKER_ENABLED` | backend | In-process worker loop (default `true`); `false` to run `python -m app.jobs.worker` separately. |

The service-role key and the Gemini API key must never appear in web or extension code.

### Database

Apply the migrations to your project (Supabase CLI):

```bash
npx supabase login
npx supabase link --project-ref <project-ref>
npx supabase db push
```

or paste the files in `supabase/migrations/` into the SQL editor in order. With a local
stack (Docker): `npx supabase start`, then `npx supabase test db` runs the pgTAP tests.

## Run

```bash
npm run dev:web        # http://localhost:3000
npm run dev:backend    # http://localhost:8000/health  (with the backend venv active)
npm run build:extension
```

With `DATABASE_URL` and `GEMINI_API_KEY` set, the backend also runs the durable worker: it
generates course skill graphs (`/courses/new` → `/courses/{id}`) and analyses captured turns.
To run the worker on its own, or drain the queue once:

```bash
cd services/backend
python -m app.jobs.worker          # loop
python -m app.jobs.worker --once   # process what is runnable now, then exit
```

Load the extension: `chrome://extensions` → enable **Developer mode** → **Load
unpacked** → select `apps/extension/dist`.

## Test

```bash
npm run test:backend      # pytest (venv active)
npm run lint
npm run typecheck         # all workspaces
npm run test:web          # vitest unit tests
npm run build             # web + extension production builds
npm run test:extension    # loads dist/ in Chromium (run `npx playwright install chromium` once)
npm run e2e:auth          # real Supabase auth round trip (see below)
```

### Auth round-trip test

`apps/web/e2e/auth-roundtrip.spec.ts` drives the real UI: sign up → dashboard → refresh →
sign out → sign in → sign out. It checks the Auth user and `profiles` row server-side, and
checks that a user's own API session cannot change its role. It needs:

- `apps/web/.env.local`
- `SUPABASE_SERVICE_ROLE_KEY` in `apps/web/.env.e2e.local` (template: `apps/web/.env.e2e.example`)
- email signups auto-confirmed on the project (Auth config `mailer_autoconfirm = true`,
  shown as "Confirm email" off where the dashboard exposes it)

Test addresses use `@mailinator.com` by default (override with `E2E_EMAIL_DOMAIN`), because
hosted Auth rejects domains that cannot receive mail. The test deletes the user it creates;
after an aborted run, `node e2e/cleanup-test-users.mjs` (from `apps/web`) removes leftovers.
Without configuration the test is skipped, never passed.

## CI

`.github/workflows/ci.yml` runs on pushes and pull requests: repository hygiene
(no env files or secrets tracked), backend lint + pytest, backend integration tests
against real Postgres (ingestion, courses, skill graph, retrieval, P3A pipeline, worker),
web lint + typecheck + unit tests + production build, extension typecheck + build +
manifest validation + Chromium tests, database migrations + pgTAP, and the auth round trip
against a local Supabase stack. Intelligence tests use a scripted fake model provider, so CI
never needs a Gemini key; the real-model acceptance is a documented local gate.
