# SkillMirror — Live Project State

This record carries live implementation status (architecture §0.1). It must never
claim an unverified gate. Architecture: [`architecture/`](architecture/).

**Last updated:** 2026-09-25

## Repository

| Field | Value |
| --- | --- |
| Repository | https://github.com/shaka9lakaboom/lappu1 |
| Default branch | `main` (unchanged until the P0 pull request is merged) |
| Development branch | `skillmirror-p0-rebuild` |
| Rebuild started from `main` | `4fc5642fb41ab524b73c2f23b9c31b4822c1256b` |
| P0 rebuild commit, CI-verified | `0a12fe9b0c8d59f7247e9eeb7bf0349323b49859`, [run 36044398696](https://github.com/shaka9lakaboom/lappu1/actions/runs/36044398696), all 6 jobs green |
| Earlier CI-verified commit | `61e29df5a16e6eb1c417700c0fc62cb69e215077`, [run 36043876138](https://github.com/shaka9lakaboom/lappu1/actions/runs/36043876138), all 6 jobs green |
| HEAD | the P0 closure commit that adds this record, on top of `0a12fe9`; its CI run is linked from the P0 pull request |

The earlier P0 attempt (`main` up to `4fc5642`, remote branch
`skillmirror-p0-foundation`) was replaced. Its project-state claims were not accurate:
no CI workflow, `.gitignore` or `benchmark/` existed. JWTs were accepted without
signature verification when no secret was set, and clients could assign themselves the
`admin` role.

## Phase

**Completed phase: P0 — Foundation. Status: COMPLETE.** Every exit gate
(architecture §19) passes: all apps run locally, the auth round trip works against the
hosted Supabase project, and CI is green.

| Gate | State | Evidence |
| --- | --- | --- |
| Web runs | PASS | Production build and `next start` locally; served against hosted Supabase in the E2E; built without env in CI |
| Backend runs | PASS | `uvicorn app.main:app` locally; `GET /health` → 200 with schema-valid JSON |
| Extension builds / loads | PASS | esbuild build + manifest validation; loaded unpacked in Chromium, service worker started, popup rendered (local and CI) |
| Auth round trip — hosted Supabase project | **PASS** | `npm run e2e:auth` on 2026-09-25: 1 passed (see below) |
| Auth round trip — local Supabase stack | PASS (CI) | `auth-e2e-local` job |
| Hosted database matches migration 0001 | PASS | See *Hosted Supabase verification* |
| Tests | PASS | See *Automated results* |
| CI | PASS | Run 36044398696 on `0a12fe9`: hygiene, backend, web, extension, database, auth-e2e-local all succeeded |

## Hosted Supabase verification (2026-09-25)

No project secrets, keys or the project ref are recorded here. The project is linked
locally with the Supabase CLI (`supabase/.temp/`, gitignored), and the linked project is
the one configured in `apps/web/.env.local`.

- **Migration:** `supabase migration list --linked` shows local `0001` = remote `0001`.
- **Catalog (read-only query through the Management API):**
  - pgvector 0.8.2 installed.
  - `public.profiles` exists with RLS enabled.
  - Policies: exactly `profiles_select_own` (SELECT) and `profiles_update_own` (UPDATE).
  - Trigger `on_auth_user_created` exists on `auth.users`; `handle_new_user()` is SECURITY DEFINER.
  - `role` defaults to `'STUDENT'::app_role`.
  - `authenticated` has UPDATE on `display_name` only: no UPDATE on `role`, no INSERT, no DELETE.
  - `anon` has no SELECT.
- **Auth round trip (`apps/web/e2e/auth-roundtrip.spec.ts`, real UI, 1 passed):**
  1. `/dashboard` redirects anonymous visitors to sign-in.
  2. Sign up returns an immediate session and lands on the dashboard.
  3. The Supabase Auth user exists (admin API).
  4. The trigger created the profile with role `STUDENT` and the given display name; the
     dashboard shows it through RLS.
  5. Self-promotion is rejected: the user's own API session gets `42501` when it
     updates `role`, and the role stays `STUDENT`.
  6. A page refresh keeps the same user.
  7. Sign out clears the `sb-*-auth-token` cookies, and `/dashboard` redirects again.
  8. Sign in with the same credentials returns to the dashboard.
  9. Sign out works again.
  10. The test user is deleted and confirmed gone. A separate check with
      `e2e/cleanup-test-users.mjs` found 0 remaining test users.
- **Hosted auth configuration change:** `mailer_autoconfirm` was `false` (signups
  required email confirmation, which made signup hit `email rate limit exceeded`). It was
  set to `true` with a single-field Management API PATCH. `external_email_enabled` (true)
  and `mailer_secure_email_change_enabled` (true) were unchanged. This is a
  development-project setting; see *Known defects*.

## Database

- Latest migration: **`0001_p0_foundation.sql`**. Covers pgvector, the `app_role` enum,
  `profiles`, RLS, the signup trigger and role immutability.
- Applied to the hosted project and to the CI Supabase CLI Postgres.
- pgTAP: `supabase/tests/0001_p0_foundation.test.sql` (13 assertions), passing in CI.

## URLs and versions

| Item | Value |
| --- | --- |
| Web (local) | http://localhost:3000 |
| API (local) | http://localhost:8000 (`/health`, OpenAPI at `/docs`) |
| Deployed web / API | none (deployment is P9) |
| Extension version | 0.1.0 (`apps/extension/manifest.json`) |
| Backend version | 0.1.0 |

## Automated results (local run on 2026-09-25, before the closure commit)

| Suite | Local (Windows, Node 22.14, Python 3.13) | CI (`0a12fe9`) |
| --- | --- | --- |
| Backend `ruff check` + `ruff format --check` | clean | pass |
| Backend pytest | **42 passed** | pass |
| Web ESLint | 0 problems | pass |
| Typecheck (web, extension, contracts, config, ui) | 5/5 clean | pass |
| Web vitest | **19 passed** | pass |
| Web + extension production builds (no Supabase env) | pass | pass |
| Extension Chromium load test | **2 passed** (with `--timeout 240000`, see defects) | pass |
| Hosted auth round trip (`npm run e2e:auth`) | **1 passed** | n/a (hosted secrets are not in CI) |
| Local-stack auth round trip | — | pass |
| Database pgTAP | not run locally (Docker not running) | pass |
| Benchmark | not applicable until P3 | — |

## Known defects and caveats

- **Auto-confirm is on for the hosted project** (`mailer_autoconfirm = true`), so anyone
  can sign up with an address they don't own. This is acceptable for development. It must be
  turned off, with a real SMTP provider configured, before any production release (P9).
- The hosted E2E uses `@mailinator.com` test addresses (hosted Auth rejects `example.com`).
  Mailinator inboxes are public, so this is safe only while auto-confirm is on and no mail is
  sent. One confirmation email was sent there during diagnosis, for an account that has since
  been deleted.
- On the development machine, closing a Playwright Chromium browser takes 90–100 s, even
  without the extension. The extension load test passes locally only with a longer
  `--timeout`; CI closes in about 3 s. This is a local environment issue, not a code defect.
- The hosted E2E is not part of CI, because the service-role key is not stored in GitHub. CI
  covers the same flow against a local Supabase stack.
- With no Supabase env, every web route (the landing page included) returns HTTP 500. This
  is deliberate fail-clearly behaviour.
- The backend CORS allow-list does not yet include the extension origin
  (`chrome-extension://<id>`). Add it in P1.
- ESLint 9 prints an end-of-support notice. It stays pinned because `create-next-app@16.3.6`
  targets ESLint 9.

## Required environment variables (names only)

- Web (`apps/web/.env.local`): `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_API_URL`
- Backend (`services/backend/.env`): `APP_ENV`, `APP_NAME`, `API_VERSION`, `CORS_ORIGINS`,
  `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET` (legacy HS256 only)
- Auth E2E (`apps/web/.env.e2e.local`): `SUPABASE_SERVICE_ROLE_KEY`, optional `E2E_EMAIL_DOMAIN`, `E2E_BASE_URL`

## Exact next action

Review and merge the P0 pull request `skillmirror-p0-rebuild` → `main`. Then start
**P1 — Capture + ingestion** (architecture §19): raw schema migration `0002`,
ChatGPTAdapter, CaptureManager, JWT-authenticated `POST /v1/events/batch` with
deduplication, and the activity page's raw status. P1's exit gate: a real ChatGPT turn is
captured, stored once, and survives a duplicate resend.
