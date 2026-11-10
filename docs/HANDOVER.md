# Sales Navigator: Build Handover

**Date:** 2026-11-10 · **Version:** 0.1.0 · **Status:** working v1, verified end to end on the demo dataset
**Read with:** [HLD.md](HLD.md) (architecture), [LLD.md](LLD.md) (design detail), [OBSERVABILITY_AND_QA.md](OBSERVABILITY_AND_QA.md), [PROMPT_CACHING_AND_GUARDRAILS.md](PROMPT_CACHING_AND_GUARDRAILS.md), [GOLDEN_TESTCASE.md](GOLDEN_TESTCASE.md)

This document is for the developer who takes over the code, and for anyone reviewing it. It covers what was built, how to run it, how to test it, how to host it, how to watch it in production, the results of every test, and what is *not* done yet.

---

## 1. What was built

### 1.1 Three tiers

```
 PRESENTATION              APPLICATION                                   DATA
 frontend/ (Next.js 16)    backend/ (FastAPI + worker, Python 3.12+)     PostgreSQL 15+ (tested on 18)
 ─────────────────────     ──────────────────────────────────────────    ──────────────────────────────
 Browser UI, :3000   ──►   REST API, :8000 (app/main.py)          ──►    3 roles: sn_owner / sn_app / sn_system
 calls the API only        Worker process (app/workers/worker.py)        Row-level security on 18 tenant tables
 (src/lib/api.ts)          both run the same modules (app/modules/*)     Job queue = search_job table (SKIP LOCKED)
```

- **The browser never touches the database.** It only calls the API (`frontend/src/lib/api.ts`).
- **The API never trusts its own WHERE clauses for tenant isolation.** Postgres row-level security does that (`backend/app/core/db.py::tenant_tx`).
- **Heavy work runs in the worker**, so web requests stay fast.

### 1.2 Backend modules (`backend/app/modules/`)

Each module is a folder with one job. Modules call each other only through their `service.py`.

| Module | What it does | Key files |
|---|---|---|
| `identity` | Sign-up, login, "who am I" | `service.py` |
| `billing` | Plans, the append-only lead-quota ledger (reserve / release / consume) | `service.py` |
| `icp` | Builds and versions the customer profile | `service.py`, `schemas.py` |
| `geo` | Turns typed regions into exact scopes. Handles aliases, ambiguity, radius, global, and the no-dead-end retry | `resolver.py`, `places_seed.py` |
| `planner` | Chooses discovery paths (rules first, then one clarifying question), estimates cost | `service.py` + `config/planner_rules.yaml` |
| `providers` | The **only** door to external data: gateway (cache, circuit breaker, budget, retry, cost ledger), demo + live adapters | `gateway.py`, `demo.py`, `brave.py`, `website.py`, `smtp_verifier.py` |
| `discovery` | Runs the chosen paths, crawls companies, gates them before any AI spend | `service.py`, `crawler.py` |
| `knowledge` | Shared knowledge store: entity resolution, encrypted contacts, evidence | `store.py`, `normalize.py` |
| `scoring` | Rule score (0–60) + AI fit (0–40), two-stage filter | `service.py` |
| `contacts` | Finds people (LinkedIn snippets / site owner), email pattern engine, suppression-aware choice | `service.py`, `email_patterns.py`, `people.py` |
| `quality` | Quality Judge: field, row and reasoning checks, PASS / DEGRADE / HOLD / REJECT | `service.py` |
| `search` | API-facing search lifecycle, orchestrator (the pipeline), Postgres job queue | `service.py`, `orchestrator.py`, `jobs.py` |
| `leads` | Lists leads, decrypts contacts on read, accepts HOLD rows, CSV export | `service.py` |
| `compliance` | Suppression lists (hashed), erasure requests | `service.py` |
| `ai` | Every AI call: offline rules or Claude, guardrails, fallback ladder, prompt caching | `service.py`, `claude.py`, `offline.py`, `guardrails.py`, `prompts.py` |
| `observability` | Writes provider_call / llm_call / search_event / error_event | `recorder.py` |
| `admin` | Read-only observability views (workspace or platform scope) | `service.py` |

Cross-cutting code lives in `backend/app/core/`: config, db, security, logging (with PII redaction), metrics, errors, auth context.

### 1.3 Frontend pages (`frontend/src/app/`)

| Route | Purpose |
|---|---|
| `/signup`, `/login` | Account access (new accounts start a 25-lead trial) |
| `/onboarding` | Describe the business → AI-drafted customer profile → edit → versioned save |
| `/searches/new` | Region (did-you-mean, ambiguity picker, forced choice after 2 tries) → plan + clarifying question → cost estimate → run |
| `/searches` | All searches, status, cost |
| `/searches/[id]` | Live progress, Delivered / Needs review / Rejected tabs, accept-from-review, Quality Report, CSV export, timeline |
| `/leads/[id]` | Why they fit (each reason with its evidence), contacts with check status, judge checks, opening line, feedback |
| `/admin` | Observability console: Overview, AI calls, Cost, Providers, Errors |
| `/settings` | Plan & usage, do-not-contact list |

Shared UI pieces live in `frontend/src/components/ui.tsx`. Design tokens (colours, light/dark) live in `frontend/src/app/globals.css`.

### 1.4 Two run modes (no code changes between them)

| Setting | `demo` / `offline` (default) | `live` / `claude` |
|---|---|---|
| `SN_DATA_MODE` | Built-in demo dataset (38 businesses incl. security traps). No network, ₹0 real cost (provider costs are *simulated* at real prices so budgets and dashboards behave as in production) | Brave Search API + real website crawl |
| `SN_LLM_MODE` | Deterministic rules stand in for the AI (same structured outputs) | Claude API: Sonnet 5.5 for ICP/fit/opener, Opus 5.5 for the Judge, with a fallback ladder |
| `SN_VERIFIER_MODE` | `fake`: answers from demo mailboxes | `smtp`: real MX/RCPT probe (needs port 25) |

---

## 2. Run it locally

### 2.1 Prerequisites

PostgreSQL 15+, Python 3.12+, Node 20+.

### 2.2 Commands

```bash
cd backend
./scripts/setup_db.sh                       # creates roles sn_owner / sn_app / sn_system + 2 databases
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                        # defaults work for local demo mode
python -m app.cli migrate && python -m app.cli seed
```

```bash
cd backend && ./scripts/dev_backend.sh      # API on :8000 + worker, Ctrl-C stops both
```

```bash
cd frontend && npm install && npm run dev   # UI on :3000 (reads NEXT_PUBLIC_API_URL, default http://localhost:8000)
```

Open http://localhost:3000, sign up, and follow the onboarding.

**Operator account.** To see the cross-workspace observability view, create a platform operator:

```bash
python -m app.cli create-admin you@yourco.example 'a-strong-password'
```

The local demo database prepared during this build already has one. Its credentials are in `backend/.demo-credentials`, a local file that is git-ignored.

> **Note from this build:** something on the build machine held port 8000, so the local preview ran the API on **8001**, with `frontend/.env.local` set to `http://localhost:8001` (see `.claude/launch.json`). On a clean machine, use 8000 and delete that line.

### 2.3 Turning on live mode

1. Get a Brave Search API key. Before production, confirm the plan includes **storage rights** (`providers/brave.py::STORAGE_ALLOWED`).
2. Set `ANTHROPIC_API_KEY`.
3. In `.env`: `SN_DATA_MODE=live`, `SN_LLM_MODE=claude`.
4. Optional: `SN_VERIFIER_MODE=smtp`, run from a host with outbound port 25 and clean reverse DNS (see HLD §4).

---

## 3. How to test

### 3.1 Commands

```bash
cd backend && . .venv/bin/activate
pytest                          # everything (73 tests, about 15 s)
pytest tests/unit               # 44 tests, no database needed
pytest tests/integration        # 26 tests against sales_navigator_test (rebuilt each run)
pytest -m golden -s             # 3 golden end-to-end cases, prints the scorecards
python -m tests.golden.grader   # same golden cases standalone; writes tests/golden/reports/*.json
```

```bash
cd frontend
npx tsc --noEmit                # type check
npm run lint                    # ESLint (incl. React hooks rules)
npm run build                   # production build
```

The tests can never touch real data. `tests/conftest.py` points all three database roles at `sales_navigator_test`, forces demo data and offline AI, and rebuilds the schema once per run.

### 3.2 What each layer covers

| Layer | Files | What it proves |
|---|---|---|
| Unit (41) | `tests/unit/` | Guardrails (injection sanitising, fencing, citation enforcement), SSRF guard (10 blocked URL kinds), log PII redaction, password hashing, AES-GCM, keyed hashes, **SMTP verifier never sends DATA**, normalisers, LinkedIn snippet parsing, persona ranking, email patterns, planner rules (6 scenarios), offline scorer/judge |
| Integration (24) | `tests/integration/` | **Row-level security** (isolation, no-workspace = no rows, cross-tenant writes rejected), quota ledger, plan upgrade top-up, tenant vs global suppression, geo resolver (state, alias lists, "and" in names, radius, ambiguity, global, **no-loop forced choice**, did-you-mean, scope membership), knowledge-store merge, contact encryption, HTTP API (auth, problem+json, versioning, 402 quota, 404 cross-tenant), every admin view on real data, **500s still readable by the browser (CORS)** |
| Golden (3) | `tests/golden/` | The whole pipeline against labelled truth (§4.2) |

### 3.3 Manual UI test script (about 5 minutes)

1. Sign up → you land on **Customer profile**.
2. Describe BlueCart: *"SaaS that gives Indian D2C brands one dashboard for inventory, orders and shipping across Shopify, Amazon and their own site."* Add seeds `pawpantry.example, chocotribe.example, leafygreens.example`. Then **Build**.
3. Click **Find leads with this profile**.
4. Type **Aurangabad** and press Check. You should be asked to pick Maharashtra or Bihar.
5. Type **Maharashtra**, set 20 leads, **Plan this search**. Check the paths and confidence, and the ₹ estimate under the ₹50 cap.
6. **Run search**. It completes in seconds: 18 delivered, 2 in review.
7. **Needs review** → **Accept** one. Delivered becomes 19, and the sidebar quota drops by exactly 1.
8. Open **Kosh Skincare**. No reason cites the planted "ignore prior instructions" text.
9. **Observability**: the tiles, AI calls, Cost, Providers and Errors all load.
10. Run a second search for "Pune, Mumbai". The Quality Report shows the earlier companies dropped as *Already delivered to you*.

---

## 4. Results (run on 2026-11-10)

### 4.1 Automated tests: 73 passed, 0 failed (after the live-mode fixes in 4.4)

| File | Tests | Result |
|---|---|---|
| unit/test_security_basics.py | 19 | ✅ all pass |
| unit/test_planner_and_ai.py | 10 | ✅ |
| unit/test_guardrails.py | 6 | ✅ |
| unit/test_normalize_and_contacts.py | 6 | ✅ |
| integration/test_geo.py | 9 | ✅ |
| integration/test_api.py | 7 | ✅ |
| integration/test_tenancy_and_billing.py | 6 | ✅ |
| integration/test_knowledge.py | 2 | ✅ |
| golden/test_golden.py | 3 | ✅ |
| unit/test_wire.py, unit/test_text_match.py, 2 new API tests (§4.4) | 5 | ✅ |
| **Total** | **73** | **✅ 73 / 73** |

Frontend: `tsc` clean, ESLint clean (0 errors, 0 warnings), `next build` succeeds (11 routes).

### 4.2 Golden case scorecards

**BlueCart.** D2C-SaaS seller, Maharashtra, 20 leads, one founder email on the do-not-contact list. **PASS: 22/22 metrics, no hard fails.**

| Metric | Result | Bar |
|---|---|---|
| Plan primary path | B2B_COMPANY (+ LOOKALIKE, SIGNAL_LED) | B2B_COMPANY, no LOCAL / AUDIENCE |
| Clarifying question | none | none |
| ICP field overlap | 100% | ≥ 80% |
| Recall of must-find companies | 87.5% (14/16) | ≥ 85% |
| Precision of delivered rows | 100% | ≥ 90% |
| Right decision-maker | 100% | ≥ 80% |
| Verified email rate | 100% | ≥ 70% |
| Wrong emails marked valid | 0 | 0 |
| Catch-all domains held for review | Urban Loom, FitFuel → HOLD (`email_status`) | expected |
| Negatives rejected with the right code | 10/10 (3 icp_mismatch, 1 company_dead, 3 out_of_region, 3 excluded_seed) | all |
| Injection sentences removed | 2 | ≥ 2 |
| Unsafe (SSRF) links blocked | 1 | ≥ 1 |
| Reasons citing invalid / injected evidence | 0 | 0 |
| Suppressed contact in output (UI + CSV) | 0 | 0 |
| Cost per delivered lead | ₹0.80 | ≤ ₹5.00 |
| Second overlapping search: completed / re-found / delivered again | COMPLETED / 15 re-found / **0 duplicates** | 0 |

Run summary: 32 companies found → 25 kept after the free gates → 20 scored above threshold → **18 PASS, 2 HOLD, 0 REJECT**. Spend ₹14.42, average delivered confidence 94%.

**DentalDesk.** Clinic software, within 25 km of Pune, 5 leads (LOCAL_BUSINESS path). **PASS: 6/6.** 5 clinics delivered, all with a business phone, each owner identified by name from the clinic's own site.

**MediReach.** An ambiguous "Healthcare" profile. **PASS.** The search stopped at NEEDS_INPUT with a two-option question. Answering "clinic owners" switched it to LOCAL_BUSINESS and ESTIMATED.

### 4.3 Bugs found by testing and fixed (all with regression tests)

| Found by | Bug | Fix |
|---|---|---|
| DentalDesk golden case | The offline judge only understood D2C wording, so every clinic was held, and the reason text was wrong for clinics | Reasons now quote the exact evidence term, and the judge checks that term against the cited evidence (any industry) |
| Golden grader review | Two DentalDesk metrics could pass with 0 rows delivered | Metrics now require at least one delivered row |
| BlueCart golden log | The knowledge-store merge failed on re-discovery (untyped array parameter), so second searches crashed. The no-duplicates check passed only because nothing ran | Explicit `text[]` casts. The grader now requires the second search to complete *and* re-find delivered companies. New test `test_knowledge.py` |
| Browser run | Admin overview crashed: Postgres SUM returns Decimal | Converted before arithmetic. New test hits every admin view after a real search |
| Browser run | A server crash reached the browser as an opaque "Failed to fetch" (no CORS header on 500) | Errors are handled inside the CORS layer and return problem+json with a request id. New test |
| Frontend lint | A `*/` inside a doc comment, and setState inside effects | Fixed. Session read through `useSyncExternalStore` |

---

### 4.4 First live-mode run (2026-11-10): what broke and what was fixed

| Symptom | Cause | Fix |
|---|---|---|
| "Global" investor search: 0 results, all "Different business type" | App still in demo mode (38 sample D2C brands/clinics, no investors) and the offline stand-in built a blank profile | Expected in demo mode. Live mode + Claude now builds a real investor profile (VCs, family offices, angels; ₹0.97) |
| Keys "connected" but nothing changed | Keys were typed into `backend/.env.example` (a template the app never reads, and tracked by git) | Moved to `backend/.env` (git-ignored, mode 600); template blanked. `/readyz` now reports `brave_key_set` / `anthropic_key_set` + warnings |
| Key in `.env` would not reach Claude | The SDK reads only the process environment | `ai/claude.py` passes the key from settings explicitly |
| Claude call rejected: "Schema is too complex" | Output schemas packed enums / bounds / nesting | Simple wire schemas (`ai/wire.py`); rules enforced in code; `test_wire.py` keeps them simple |
| Short keywords ("ev") matched "every", "event" | Substring matching | Whole-word matching (`ai/text_match.py`) |
| Blank profile still searched and spent money | No guard | Search refused before spend: `profile_too_vague` (422) |
| Live mode without Brave key returned an empty search | Planner fell back to "audience only" | Search refused before spend: `no_data_source` (503) |
| Tests read the real `.env` | Not hermetic | `conftest.py` blanks real keys: tests can never call a paid API |

## 5. Guardrails: where each one is and what proves it

| Guardrail | Implemented in | Proven by |
|---|---|---|
| Tenant isolation in the database (RLS) | `migrations/001_schema.sql` §5, `core/db.py::tenant_tx` | `test_tenancy_and_billing.py` (3 tests), `test_api.py::test_cross_tenant_access_is_not_found` |
| Prompt-injection sentence removal | `ai/guardrails.py::sanitize_evidence`, `discovery/crawler.py` | `test_guardrails.py`, golden `injections_removed` |
| Untrusted data fenced in prompts, operator rules in system prompt | `ai/guardrails.py::fence`, `ai/prompts.py` | `test_guardrails.py::test_fence_cannot_be_closed_from_inside` |
| Every reason must cite our own evidence | `ai/guardrails.py::enforce_citations` (both AI modes) | `test_guardrails.py::test_citations_enforced`, golden `evidence_grounding_bad_cites = 0` |
| Independent Quality Judge; nothing delivered unjudged | `quality/service.py` (judge unavailable → HOLD) | golden decisions, `test_planner_and_ai.py::test_offline_judge_flags_unsupported_reason` |
| Structured outputs only; refusal handling; model fallback ladder | `ai/claude.py` (`messages.parse`, `stop_reason` check, `config/llm.yaml` ladders) | code review (Claude mode needs a key to run live) |
| SSRF guard on every fetch and redirect | `providers/website.py::is_safe_url` | `test_security_basics.py` (13 cases), golden `unsafe_links_blocked` |
| SMTP checks never send email | `providers/smtp_verifier.py` (no DATA) | `test_smtp_verifier_never_sends_data` |
| PII never in logs | `core/logging.py::redact_pii` (always on) | `test_redaction_*` |
| Contacts encrypted at rest; lookups by keyed hash | `core/security.py`, `knowledge/store.py` | `test_knowledge.py::test_contacts_are_encrypted_and_deduplicated` |
| Suppression (tenant + global erasure), re-checked at export | `compliance/service.py`, `contacts/service.py`, `leads/service.py::export_csv` | `test_suppression_tenant_and_global`, golden `suppressed_or_forbidden_contacts_in_output = 0` |
| Never deliver the same record twice to a customer (permanent, by identity) | `delivered_record` table + unique key, `discovery/service.py::_gate`, `quality/service.py`, `orchestrator.py::_deliver` (savepoint) | golden `duplicates_on_second_search = 0` with 15 re-found |
| Budget cap per search; quota reserved and released; pay only for delivered rows | `providers/gateway.py::BudgetTracker`, `billing/service.py`, `orchestrator.py::_complete` | `test_quota_grant_reserve_release`, `test_quota_exceeded_is_402`, UI run (19 delivered → exactly 19 charged) |
| Circuit breaker + retry per provider | `providers/gateway.py` | code review |
| Region input can't dead-end | `geo/resolver.py` (`forced_choice` after 2 attempts), new-search page | `test_geo.py::test_unresolved_never_loops` |
| Password hashing (argon2id), signed short-lived tokens, role checks | `core/security.py`, `core/context.py` | `test_password_hashing`, `test_auth_flow_and_problem_json` |

---

## 6. Observability: how to watch it

| What | Where |
|---|---|
| **Console (UI)** | `/admin`. Tabs: **Overview** (spend, cost per lead, cache hit rate, AI calls / failures, leads, searches, errors) · **AI calls** (per task and model: calls, success, tokens in / cached / out, cache ratio, ₹, fallbacks, evidence drops, p95) · **Cost** (today vs platform daily cap, daily bars, by provider/operation) · **Providers** (error rate, cache hits, p95, ₹) · **Errors** (type, location, message, request id). A workspace admin sees their own workspace; a platform operator can tick **All workspaces**. |
| **Per-search timeline** | Search page → **Timeline** tab, or `GET /v1/admin/searches/{id}/timeline`. Stages, provider calls (with cache hits and cost), AI calls, guardrail events, compliance skips, and each Quality Judge decision, in order. |
| **Prometheus metrics** | `GET /metrics` (blocked from the public in `deploy/Caddyfile`). Counters and histograms: `sn_http_requests_total`, `sn_http_latency_seconds`, `sn_provider_calls_total`, `sn_provider_cost_micro_usd_total`, `sn_llm_calls_total`, `sn_llm_tokens_total{kind=in\|cached\|out}`, `sn_quality_decisions_total`, `sn_search_outcome_total`, `sn_delivered_leads_total`. Point Grafana at it. |
| **Logs** | One JSON line per event on stdout (systemd → journald: `journalctl -u sn-api -f`). Every line carries `request_id`. Emails and phones are masked. |
| **Errors** | Every unhandled error is written to `error_event` (Errors tab) with the request id that the user also sees in the UI. |
| **Health** | `GET /healthz` (process up) and `GET /readyz` (database reachable, current modes). Point an uptime monitor at `/readyz`. |
| **Raw SQL** (operator) | Connect as `sn_system`. Useful tables: `provider_call`, `llm_call`, `search_event`, `error_event`, `quota_ledger`, `audit_log`. |

**Prompt caching in Claude mode:** the AI calls tab shows the *cache ratio* per task. If it drops to ~0, something is changing the cached prefix (see PROMPT_CACHING_AND_GUARDRAILS.md §2.3).

---

## 7. Hosting (Stage 1: one VPS, about 0–150 customers)

Tested here: everything except the server setup itself. Docker was not available on the build machine, so the deploy files below (systemd + Caddy) are provided but **were not run in this environment**.

1. **Server.** Ubuntu 24.04, 4–8 vCPU / 16–32 GB, India region (e.g. Hostinger VPS, E2E Networks, AWS Mumbai). Create a `salesnav` user and clone the repo to `/opt/sales-navigator`.
2. **PostgreSQL 16+.** Install it, then run `backend/scripts/setup_db.sh` with **strong passwords**:
   ```bash
   SN_OWNER_PW=… SN_APP_PW=… SN_SYSTEM_PW=… ./scripts/setup_db.sh
   ```
3. **Backend.**
   ```bash
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   ```
   Create `.env` from `.env.example`:
   - Set the three database URLs.
   - Paste the output of `python -m app.cli gen-secrets` (JWT secret, AES key, HMAC pepper).
   - Set `SN_ENV=prod`, the modes and API keys, and `SN_CORS_ORIGINS=https://app.example.com`.

   Then run `python -m app.cli migrate && python -m app.cli seed && python -m app.cli create-admin …`.
4. **Frontend.**
   ```bash
   cd frontend && npm ci && NEXT_PUBLIC_API_URL=https://api.example.com npm run build
   ```
5. **Services.**
   ```bash
   sudo cp deploy/systemd/*.service /etc/systemd/system/
   sudo systemctl enable --now sn-api sn-worker sn-web
   ```
   The API runs `migrate` before every start.
6. **HTTPS.** Install Caddy, copy `deploy/Caddyfile` with your domains, and reload. Certificates are automatic.
7. **Backups.** Nightly `pg_dump -Fc sales_navigator` to object storage (cron), plus a quarterly restore drill (HLD §9.4).
8. **Monitoring.** Uptime check on `https://api…/readyz`. Prometheus scrapes `127.0.0.1:8000/metrics`. Alert rules are in OBSERVABILITY_AND_QA.md §A.3 / §B.5.
9. **Scaling (Stage 2).** Move Postgres to a managed instance, run 2+ API nodes behind the load balancer, and add more `sn-worker` instances. No code changes needed.

**Security checklist before go-live:**
- [ ] `SN_ENV=prod`. The app then refuses to run without `SN_ENCRYPTION_KEY`, and `reset-db` is disabled.
- [ ] Unique database passwords.
- [ ] `.env` readable only by `salesnav`.
- [ ] `/metrics` not public.
- [ ] The Brave plan includes storage rights before enabling the web-search cache.

---

## 8. Configuration reference

| File | Controls |
|---|---|
| `backend/.env` (`.env.example`) | Database URLs, secrets, data / AI / verifier modes, FX rate, platform daily spend cap, CORS |
| `config/plans.yaml` | Plans (₹, leads, seats). Seeded with `python -m app.cli seed` |
| `config/llm.yaml` | Model per task + fallback ladder, prices, fit batch size, token budget |
| `config/planner_rules.yaml` | Path rules (safe expressions), ambiguous industries, minimum confidence |
| `config/quality.yaml` | PASS / HOLD thresholds, confidence weights, permanent no-redelivery switch |
| `config/scoring_weights.yaml` | Rule-score weights, company threshold |
| `config/pipeline.yaml` | People per company, email candidates, pool size, budget factor, email-format priors |

---

## 9. What is NOT done yet (honest list)

| Area | State in v1 | Next step |
|---|---|---|
| Payments | Plan switch works with no payment step | Razorpay subscriptions + webhooks + GST invoices (LLD §6.13) |
| Live place data | LOCAL_BUSINESS runs on demo data only. In live mode the planner skips it (no source) | Overture/DuckDB or Google Places adapter (`providers/live.py::place_search`) |
| Claude mode | Implemented (structured outputs, caching, ladder, refusal handling) but **not run against the real API** in this build (no key used) | Run the golden cases with `SN_LLM_MODE=claude` and a small budget. Compare scorecards |
| Calibration | Identity mapping (`quality/service.py::calibrate`) | Fit isotonic regression once there are ≥ 300 labelled outcomes |
| HOLD adjudication | Config switch exists, off. Not implemented | `quality/adjudication.py` per PROMPT_CACHING_AND_GUARDRAILS §5.2b |
| Sessions | JWT (HS256, 60 min) in localStorage | Rotating refresh token in an httpOnly cookie (LLD §6.15) |
| Live updates | The search page polls every 2 s | Server-sent events (LLD §5.3) if polling load matters |
| Background jobs | Postgres queue (no Redis / Celery) | Fine to ~500 customers. Swap in `search/jobs.py` if ever needed |
| CRM exports | CSV only | Google Sheets / HubSpot / Zoho (LLD §6.14) |
| Search budget vs AI | Provider calls are blocked at the cap. AI spend is added afterwards (`_persist_spend`) | Pre-check the AI token budget per call |
| Dashboards | Cost bar chart is basic | Add Grafana on `/metrics` for history |

### Differences from the design documents (deliberate)
- **Job queue.** Postgres `SKIP LOCKED` instead of Celery + Redis: one less service for a single developer.
- **Live updates.** Polling instead of SSE.
- **Token signing.** HS256 instead of EdDSA.
- **Configuration.** Per-module YAML config instead of a single settings service.

The module boundaries are exactly as in the LLD, so each of these can be swapped later inside one module.

---

## 10. Extending it

- **New data source:** add a class with the four provider methods (`providers/base.py::DataProvider`), register it in `providers/gateway.py::build_gateway`, and set its unit cost in `UNIT_COST_MICRO_USD`. Nothing else changes.
- **New discovery path:** add a runner in `discovery/service.py`, a rule in `config/planner_rules.yaml`, and a label in `planner/service.py::PATH_LABELS`.
- **Stricter / looser quality:** edit `config/quality.yaml`, then run `pytest -m golden` to see the effect before shipping.
- **New golden case:** copy `tests/golden/case_bluecart.json`, add a runner in `tests/golden/grader.py`, and add a test in `test_golden.py`.
