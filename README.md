# Sales Navigator

Finds customers for any business in any region: verified decision-makers, contacts, LinkedIn profiles, and an **evidence-backed reason** why each one fits. Built as a three-tier, multi-tenant SaaS for the Indian market.

```
frontend/   Presentation tier - Next.js 16 + TypeScript + Tailwind  (UI on :3000)
backend/    Application tier  - FastAPI + background worker, Python   (API on :8000)
            Data tier         - PostgreSQL with row-level security   (backend/migrations/)
deploy/     systemd units + Caddyfile for a single-VPS deployment
docs/       Design (SYSTEM_DESIGN, HLD, LLD, ...) and the build HANDOVER
```

## Quick start (demo mode: no API keys needed)

```bash
cd backend
./scripts/setup_db.sh
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env
python -m app.cli migrate && python -m app.cli seed
./scripts/dev_backend.sh            # API + worker
```

```bash
cd frontend && npm install && npm run dev
```

Open http://localhost:3000 and sign up.

## Tests

```bash
cd backend && . .venv/bin/activate && pytest     # 68 tests: unit, integration (real Postgres), golden end-to-end
```

## Documentation

**Start with [docs/HANDOVER.md](docs/HANDOVER.md).** It covers how to run, test, host and monitor the system, the latest test and golden-case results, every guardrail and the test that proves it, and what is not done yet.
