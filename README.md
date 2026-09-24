# SkillMirror Monorepo

SkillMirror is a mastery-based learning engine for capturing AI interactions, attributing skills, and measuring genuine mastery.

This repository is in **Development Phase P0 — Foundation**.

---

## Workspace Structure

```
skillmirror/
├── apps/
│   ├── web/            # Next.js + TypeScript + Tailwind CSS + shadcn/ui
│   └── extension/      # Chrome Manifest V3 TypeScript Extension
├── services/
│   └── backend/        # Python FastAPI service (GET /health, Supabase JWT auth)
├── packages/
│   ├── contracts/      # Canonical domain enums, types, and API interfaces
│   ├── config/         # Shared configuration constants
│   └── ui/             # Shared UI utilities and baseline components
├── supabase/
│   ├── migrations/     # Numbered SQL migrations (20260924000000_p0_initial_schema.sql)
│   ├── seed/           # Seed data
│   └── config.toml     # Supabase project configuration
├── benchmark/          # Benchmark cases, labels, and runners placeholders
├── docs/               # Architecture docs & live project state record
├── .github/
│   └── workflows/
│       └── ci.yml      # GitHub Actions CI Workflow
└── README.md
```

---

## Prerequisites

- **Node.js**: v20+ or v24+
- **Python**: v3.10+ (v3.11 recommended)
- **Supabase CLI**: Optional for local database management

---

## Installation

```bash
# Install Node.js monorepo dependencies
npm install

# Setup Python virtual environment for backend
python -m venv services/backend/venv

# Activate virtual environment
# Windows (PowerShell):
. services/backend/venv/Scripts/Activate.ps1
# macOS/Linux:
# source services/backend/venv/bin/activate

# Install backend dependencies
pip install -r services/backend/requirements.txt
```

---

## Environment Setup

Copy `.env.example` templates to `.env`:

```bash
cp .env.example .env
cp apps/web/.env.example apps/web/.env
cp services/backend/.env.example services/backend/.env
cp apps/extension/.env.example apps/extension/.env
```

Populate the `.env` files with your Supabase credentials:
- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `SUPABASE_SERVICE_ROLE_KEY`

---

## Local Development Startup

### 1. Web Application (Next.js)
```bash
npm run dev:web
```
App will start on `http://localhost:3000`.

### 2. Backend Service (FastAPI)
```bash
npm run dev:backend
# Or directly:
uvicorn app.main:app --reload --app-dir services/backend
```
Backend API will start on `http://localhost:8000`.
Health endpoint: `http://localhost:8000/health`.

### 3. Chrome Extension Build & Load
```bash
npm run build:extension
```
Build output will be created in `apps/extension/dist`.

**Loading in Chrome:**
1. Open Chrome and navigate to `chrome://extensions/`.
2. Enable **Developer mode** (toggle in top right).
3. Click **Load unpacked**.
4. Select the `apps/extension/dist` folder.

---

## Supabase Setup

Local Supabase (optional):
```bash
supabase start
supabase db reset
```

The initial migration `supabase/migrations/20260924000000_p0_initial_schema.sql` enables `pgvector` extension and configures the `profiles` table with RLS policies.

---

## Testing & Validation

```bash
# Run backend pytest suite
npm run test:backend

# Run web app tests
npm run test:web

# Run typechecks
npm run typecheck

# Build web & extension
npm run build
```

---

## CI / GitHub Actions

GitHub Actions workflow is located at `.github/workflows/ci.yml`. It runs:
- **Backend CI**: Python setup, dependency install, pytest suite
- **Web CI**: Node setup, dependency install, typecheck, Next.js production build
- **Extension CI**: Node setup, dependency install, typecheck, Vite build

---

## Project State & Phase Tracking

Current live project state is documented at [docs/project-state.md](file:///c:/Users/Probartika/Desktop/skillmiror/docs/project-state.md).
