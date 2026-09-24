# SkillMirror — Live Project State

This record carries live implementation status (architecture §0.1). It must never
claim an unverified gate. Architecture: [`architecture/`](architecture/).

**Last updated:** 2026-09-25

## Repository

| Field | Value |
| --- | --- |
| Repository | https://github.com/shaka9lakaboom/lappu1 |
| Default branch | `main` (not modified by the rebuild) |
| Development branch | `skillmirror-p0-rebuild` (not merged) |
| Rebuild started from `main` | `4fc5642fb41ab524b73c2f23b9c31b4822c1256b` |
| Last CI-verified commit | `61e29df5a16e6eb1c417700c0fc62cb69e215077` |
| HEAD | the commit that adds this record (docs only, on top of `61e29df`); see `git log -1` |

The earlier P0 attempt (`main` up to `4fc5642`, remote branch
`skillmirror-p0-foundation`) was replaced. Its project-state claims were not accurate:
no CI workflow, `.gitignore` or `benchmark/` existed. JWTs were accepted without
signature verification when no secret was set, and clients could assign themselves the
`admin` role.

## Phase

**Current phase: P0 — Foundation. Status: NOT COMPLETE. One gate is blocked on
human configuration of the hosted Supabase project.**

| Gate | State | Evidence |
| --- | --- | --- |
| Web runs | PASS | Production build and `next start` locally (no env → HTTP 500 with a named `SupabaseEnvError`, by design); built and served against a real Supabase stack in CI |
| Backend runs | PASS | `uvicorn app.main:app` locally; `GET /health` → 200 with schema-valid JSON |
| Extension builds / loads | PASS | esbuild build + manifest validation; loaded unpacked in Chromium, service worker started, popup rendered (local and CI) |
| Auth round trip — local Supabase stack | PASS (CI) | `auth-e2e-local` job: sign up → Auth user → STUDENT profile → dashboard → refresh → sign out → sign in → sign out |
| **Auth round trip — hosted Supabase project** | **BLOCKED** | No project credentials available; see *Human actions* |
| Tests | PASS | See below |
| CI | PASS | Run [36043876138](https://github.com/shaka9lakaboom/lappu1/actions/runs/36043876138) on `61e29df`: all 6 jobs succeeded (read from the GitHub API) |

## Database

- Latest migration: **`0001_p0_foundation.sql`**. Covers pgvector, the `app_role`
  enum, `profiles`, RLS, the signup trigger and role immutability.
- Applied: to the CI Supabase CLI Postgres only. **Not applied to a hosted project.**
- pgTAP: `supabase/tests/0001_p0_foundation.test.sql` (13 assertions), passing in CI.

## URLs and versions

| Item | Value |
| --- | --- |
| Web (local) | http://localhost:3000 |
| API (local) | http://localhost:8000 (`/health`, OpenAPI at `/docs`) |
| Deployed web / API | none (deployment is P9) |
| Extension version | 0.1.0 (`apps/extension/manifest.json`) |
| Backend version | 0.1.0 |

## Automated results (on `61e29df`)

| Suite | Local (Windows, Node 22.14, Python 3.13) | CI (ubuntu-latest) |
| --- | --- | --- |
| Backend `ruff check` + `ruff format --check` | clean | pass |
| Backend pytest | **42 passed** | pass |
| Web ESLint | 0 problems | pass |
| Typecheck (web, extension, contracts, config, ui) | 5/5 clean | pass |
| Web vitest | **19 passed** (2 files) | pass |
| Web production build (no Supabase env) | pass | pass |
| Extension build + manifest validation | pass | pass |
| Extension Chromium load test | **2 passed** | pass |
| Database pgTAP | not run locally (Docker not running) | pass |
| Auth round trip (`e2e:auth`) | skipped (no project credentials) | pass against local stack |
| Benchmark | not applicable until P3 | — |

## Known defects and caveats

- The hosted-project auth round trip has not been demonstrated (gate BLOCKED).
- pgTAP and the auth E2E have only run in CI, never on the local Windows machine.
- With no Supabase env, every web route (the landing page included) returns HTTP 500.
  This is deliberate fail-clearly behaviour.
- The backend CORS allow-list does not yet include the extension origin
  (`chrome-extension://<id>`). Add it in P1 when the extension calls the API.
- ESLint 9 prints an end-of-support notice. It stays pinned because `create-next-app@16.3.6`
  targets ESLint 9.
- On the development machine, a transient out-of-memory error ("paging file is too
  small") failed one web build. The retry passed; this was an environment issue, not a
  code issue.
- `main` still contains the old P0 files and inaccurate project-state until this branch
  is merged.

## Required environment variables (names only)

- Web (`apps/web/.env.local`): `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_API_URL`
- Backend (`services/backend/.env`): `APP_ENV`, `APP_NAME`, `API_VERSION`, `CORS_ORIGINS`,
  `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET` (legacy HS256 only)
- Auth E2E (`apps/web/.env.e2e.local`): `SUPABASE_SERVICE_ROLE_KEY`, optional `E2E_EMAIL_DOMAIN`, `E2E_BASE_URL`

## Human actions required

1. Create (or choose) a Supabase project for development.
2. Authentication → Sign In / Providers → Email: keep Email enabled and **disable
   "Confirm email"** for this dev project. The automated round trip cannot open an inbox.
3. Authentication → URL Configuration: set Site URL to `http://localhost:3000` and add
   `http://localhost:3000/auth/callback` to the redirect URLs.
4. Apply the migration: `npx supabase login`, `npx supabase link --project-ref <ref>`,
   `npx supabase db push` (or run `0001_p0_foundation.sql` in the SQL editor).
5. Create `apps/web/.env.local` (URL + anon/publishable key, `NEXT_PUBLIC_API_URL=http://localhost:8000`)
   and `apps/web/.env.e2e.local` (`SUPABASE_SERVICE_ROLE_KEY`).
6. Run `npm run e2e:auth` and share the output, or share the credentials through local
   env files so the gate can be run in a session.

## Exact next action

Run the hosted-project auth round trip (`npm run e2e:auth`) once the human actions
above are done. If it passes, mark P0 COMPLETE here, then open a PR
`skillmirror-p0-rebuild` → `main`. Do not start P1 (capture + ingestion) before then.
