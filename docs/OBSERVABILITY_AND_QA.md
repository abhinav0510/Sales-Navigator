# Observability, Cost Monitoring & Quality Assurance

**Version:** 1.0 · **Date:** 2026-11-10
**Expands:** [HLD.md](HLD.md) §14, [LLD.md](LLD.md) §11 and §13. This is the operator-facing reliability spec: how admins see what AI (agent) calls did, what everything costs, what broke, and how the app is proven good **before** it reaches production.

**Audience:** the platform operator (the client's staff) and the maintaining developer.

---

## Part A — Observability for agent (LLM) calls

Every call to Claude is an "agent call". These are the most expensive and the least predictable part of the system, so each one is recorded end to end.

### A.1 Every agent call is a traced, stored record
A single table + a trace span per call. Nothing calls Claude except through `providers/adapters/claude.py`, so instrumentation lives in one place.

```sql
CREATE TABLE llm_call (
  id uuid PRIMARY KEY,
  workspace_id uuid, search_id uuid, candidate_id uuid,     -- what it was for
  task text NOT NULL,              -- icp | website_facts | fit | opener | judge | planner_tiebreak
  model text NOT NULL, prompt_version text NOT NULL,
  trace_id text NOT NULL, parent_span text,
  tokens_in int, tokens_cached int, tokens_out int,
  cost_micro_usd bigint NOT NULL,
  latency_ms int, status text NOT NULL,     -- ok | schema_error | refusal | rate_limited | timeout | error
  retries int NOT NULL DEFAULT 0,
  schema_valid boolean, evidence_enforced_drops int,        -- reasons dropped for missing evidence
  request_digest bytea,            -- hash of the rendered prompt (dedupe + cache audit, NOT the raw PII prompt)
  input_ref text, output_ref text, -- object-storage keys, redacted, 30-day retention, sampled
  created_at timestamptz DEFAULT now()
) PARTITION BY RANGE (created_at);   -- monthly partitions
CREATE INDEX ON llm_call (workspace_id, created_at DESC);
CREATE INDEX ON llm_call (task, status, created_at DESC);
```

- **Prompt/response capture:** full rendered prompt and raw output are saved to object storage **with PII redacted** (emails, phones, person names masked), **sampled** (100% of errors and schema failures, 5% of successes), kept 30 days. This is what lets an admin answer "why did the judge reject this lead?" by reading the exact input and output.
- **`request_digest`** lets the operator see duplicate prompts (a sign a cache is missing) without storing readable personal data.
- **Trace linkage:** `trace_id` ties the agent call to the HTTP request and the search that caused it, so one click goes request → search → stage → agent call.

### A.2 Agent-call dashboard (operator console → "AI")
| Panel | Shows | Why it matters |
|---|---|---|
| Calls by task | Volume + success rate for icp / fit / judge / opener / planner | Spot a task that suddenly fails |
| Token mix | input vs **cached** vs output tokens per task | Proves prompt caching is working; cached tokens are far cheaper |
| Cost by task & model | ₹/day per task, per model | Find the expensive task to optimise |
| Latency p50/p95/p99 | per task | Slow judge = slow delivery |
| Schema-failure rate | % of calls whose JSON failed validation | A model/prompt regression shows here first |
| Evidence-drop rate | reasons removed for unsupported citations | Rising = the generator is hallucinating more |
| Refusal/guardrail rate | calls the model declined | Prompt or content issue |
| Retry rate | calls that needed a repair retry | Cost and latency leak |
| Judge vs generator | agreement %, judge override rate | The core fairness signal |

### A.3 Agent-call health alerts
| Alert | Condition | Severity |
|---|---|---|
| Schema failures spike | > 3% over 15 min (baseline ~0.5%) | High |
| Evidence-drop spike | drop rate > 2× the 7-day average | High |
| Judge override spike | > 25% of rows (baseline ~10%) | High — suspect a generator regression |
| Latency breach | p95 > 8 s for 10 min | Medium |
| Model error/refusal | > 5% over 10 min | High |
| Prompt-cache collapse | cached-token ratio drops > 50% | Medium (cost) |

---

## Part B — Cost monitoring system

Goal: **no surprise bills, and cost visible per task, per provider, per customer, per search — in near real time.**

### B.1 The cost ledger (single source of truth)
Every paid action writes one `provider_call` row (LLD §3.7) and every agent call an `llm_call` row, both with `cost_micro_usd`. Cost is **money, in integer micro-USD**, converted to ₹ with `FX_USD_INR` for display. Nothing estimates cost after the fact; it is recorded at the moment of the call.

### B.2 Real-time counters (Redis, flushed to Postgres)
```
cost:day:{YYYY-MM-DD}                        -- platform spend today
cost:day:{date}:provider:{p}                 -- per provider today
cost:search:{id}                             -- live spend of a running search (drives the budget lock)
cost:ws:{id}:period:{YYYY-MM}                -- per customer this billing period
```
These power the live "₹ spent" bar during a search and the daily spend figure, without scanning tables.

### B.3 Budgets and caps (enforced, not just watched)
| Cap | Where | Behaviour when hit |
|---|---|---|
| **Per-search budget** | gateway checks `cost:search:{id}` before each paid call | Search → `PAUSED_BUDGET`, user asked to raise it |
| **Per-workspace daily spend cap** | planner + gateway | New searches blocked for that workspace, admin notified |
| **Platform daily spend cap** (operator-set) | gateway | **Kill-switch:** non-essential paid calls stop, free sources only, page the operator. Protects against a runaway bug or abuse |
| **Per-provider daily cap** | gateway | That provider is skipped, others continue |

### B.4 Cost dashboard (operator console → "Cost")
- **Today vs forecast** line, with a shaded budget band.
- **Cost per delivered lead** (the headline unit economic), split by path and by region. Target ≤ ₹5 in free-first mode.
- **Cost per verified contact, per provider** — this is the number used to reorder the waterfall (§B.6).
- **Spend by customer** — flags accounts that cost more than they pay (a margin leak).
- **Cache savings** — ₹ saved by cache + knowledge-store reuse (reported as "would-have-cost minus actual").
- **Month-to-date** vs the same point last month.

### B.5 Cost alerts
| Alert | Condition |
|---|---|
| Daily spend > 120% of forecast | per day |
| A single search's spend > 3× its estimate | per search (likely a bug or a bad plan) |
| Cost per delivered lead > ₹8 (7-day avg) | unit economics slipping |
| A customer's cost > 80% of their plan price | margin warning |
| Provider price change detected | actual unit cost ≠ configured unit cost |

### B.6 Monthly cost review (automated report)
A beat job emails the operator: spend by provider and task, cost per lead by region, the most expensive searches, cache hit rate, and a **recommended waterfall reorder** (cheapest cost-per-verified-contact first). The operator applies it with one click in admin.

---

## Part C — Logging and error tracking

### C.1 Structured logs
- JSON logs (structlog) with correlation fields on **every** line: `ts, level, msg, request_id, trace_id, workspace_id, user_id, search_id, task, provider, duration_ms`.
- **PII redaction filter** is mandatory and tested: emails → `r***@acme.in`, phones → `+91******3210`, person names → initials. A unit test asserts no raw email/phone pattern ever reaches a log sink.
- Log levels: `DEBUG` (dev only), `INFO` (state changes, deliveries), `WARNING` (degraded: source skipped, catch-all), `ERROR` (handled failure), `CRITICAL` (page someone).

### C.2 Error tracking (Sentry)
- Every unhandled exception and every `ERROR`/`CRITICAL` log goes to Sentry with the correlation fields as tags, so an admin filters by `workspace_id` or `search_id`.
- **Release tagging:** errors are tagged with the git SHA, so a spike after a deploy is obvious.
- **Grouping:** by exception type + module. Alerts on a new error type, or a known one breaching a rate.

### C.3 "Why did this search misbehave?" — the audit trail an admin actually uses
For any `search_id`, the operator console assembles a single timeline from existing tables (no new logging needed):
```
search_event (state changes) + provider_call + llm_call + judgment + quota_ledger
→ one ordered view: what ran, what each step cost, what the AI decided, what was delivered/held/rejected and why.
```
This is the primary debugging tool. Example: *"Search delivered 40 of 50"* → the timeline shows discovery paused at the budget cap, 12 rows rejected (reason codes), and the backfill that was skipped — all on one screen.

### C.4 Health endpoints & uptime
- `/healthz` (liveness), `/readyz` (DB + Redis + verifier reachable), `/metrics` (Prometheus).
- An external uptime monitor (Uptime Kuma) pings `/readyz` and the public site every minute.
- A **synthetic canary** runs a tiny end-to-end search every 15 minutes against a fixed fixture business/region and alerts if it fails or its result quality drifts — this catches breakage a health check cannot.

---

## Part D — Evaluations (prove quality, continuously)

Evals are version-controlled datasets with pass thresholds that **gate deployment**. Each has an owner and is reviewed quarterly.

### D.1 Eval suites
| Suite | What it tests | Dataset | Pass threshold (CI blocks below it) |
|---|---|---|---|
| **Planner** | Correct discovery path chosen | 150 labelled ICP+region cases (incl. ambiguous: "healthcare", "education", "fintech") | ≥ 90% correct primary path; every ambiguous case triggers a clarifying question |
| **ICP quality** | ICP matches a human-written one | 40 businesses with a reference ICP | ≥ 80% field overlap; no hallucinated industries |
| **Fit scoring** | AI fit agrees with human raters | 200 company–ICP pairs, labelled strong/medium/weak/no | ≥ 80% agreement (±1 band); correlation ≥ 0.7 |
| **Quality Judge** | PASS rows are really good; good rows aren't wrongly rejected | Labelled good + deliberately broken rows (wrong employer, bounced email, unsupported reason, stale job) | PASS precision ≥ 90%; false-reject ≤ 15% |
| **Evidence grounding** | No invented "why they fit" | 100 rows with known evidence | 0 reasons survive without a valid evidence citation |
| **Entity resolution** | Duplicates merged, distinct kept | 500 labelled company pairs | F1 ≥ 0.95 |
| **Email pattern** | Right email guessed | 300 known (name, email, domain) incl. Indian names, initials, single names | ≥ 70% top-1 where a pattern exists; 0 wrong "valid" after SMTP |
| **Geo** | Region strings resolve correctly | 300 Indian + global place strings incl. aliases, ambiguous, radius | ≥ 98% correct; 100% of unresolvable cases exit without a loop |
| **Calibration** | Confidence means what it says | the gold set | Expected Calibration Error ≤ 0.07 |
| **Prompt-injection / safety** | Malicious website text can't steer the agent | pages with planted "ignore instructions", fake data | 0 instances of the agent following injected instructions |

> **The end-to-end golden test case** (one fully labelled business → expected plan, ICP, leads, rejections, injection traps and thresholds) is specified in [GOLDEN_TESTCASE.md](GOLDEN_TESTCASE.md), with the machine-readable fixture in [golden/case_bluecart.json](golden/case_bluecart.json). It is the single test that proves the whole pipeline together and gates every release.

### D.2 Gold set (the accuracy proof handed to the client)
- 200 hand-verified contacts each for **India-B2B, India-local, Global-B2B** (emails sent + bounces tracked, numbers called).
- Run **quarterly** and after any major provider change.
- Produces the published numbers: deliverable-email rate, right-decision-maker rate, phone coverage, human-rated fit. These are the figures in the client contract (behaviour + measured accuracy, never a blanket data guarantee).

### D.3 How evals run
- **In CI** (every PR): the deterministic suites + LLM suites run against **recorded fixtures** (no live spend). A regression beyond threshold **fails the build**.
- **Nightly** (staging, tiny real budget): the same suites against live providers, to catch vendor drift.
- **LLM-as-judge for scoring evals** uses a *different* model than the one under test, plus a human-labelled anchor set to keep the judge honest.
- **Variance:** LLM evals run 3 seeds; the reported score is the mean, and a wide spread is itself a finding.
- **No eval regression ships.** A model or prompt change that drops any suite > 3 points is blocked until fixed or explicitly waived by the operator with a logged reason.

---

## Part E — Pre-production testing best practices (the release gate)

Nothing reaches production until all of this is green. This is the checklist the maintaining developer runs.

### E.1 Automated gates (CI/CD)
1. **Static:** ruff (lint), mypy --strict, eslint/tsc, import-linter (module boundaries), bandit + semgrep (SAST).
2. **Unit tests:** ≥ 85% coverage on core (resolution, scoring, waterfall, quality, billing math, geo, redaction).
3. **Contract tests:** every provider adapter against recorded responses (vcrpy). Catches our-side breakage free.
4. **Integration tests:** full pipeline on fake providers + Postgres/Redis testcontainers.
5. **Eval suites (Part D)** against fixtures.
6. **Security:** pip-audit, npm audit, secret-scan (gitleaks), dependency pins.
7. **DB migrations:** apply forward + rollback on a copy; expand/contract checked.
8. **Build** container images, generate SBOM.

### E.2 Staging soak (before every production release)
- Deploy to staging with **real providers and tiny budget caps**.
- **Smoke test:** sign-up → ICP → search → leads → export → subscribe (Razorpay test mode).
- **Nightly live evals** must be green for the last run.
- **Load test** (Locust) to the 20k-leads/day profile: check queue age, p95 latency, error rate, autoscaling, and that budget caps hold under load.
- **Canary** runs for 24–48 h; watch the agent-call, cost, and error dashboards for drift.

### E.3 Data-quality tests specific to this product
- **Bounce test:** send to 100 freshly delivered "valid" emails; the bounce rate must be ≤ 5% (else the SMTP verifier or pattern engine regressed).
- **No-leak test:** scan a staging export for suppressed/erased contacts — must be zero.
- **No-duplicate test:** run two overlapping searches in one workspace; assert no record (by any identity key) appears in both deliveries. Must be zero duplicates.
- **Tenant-isolation test:** automated attempt to read another workspace's data via API and SQL — must fail (RLS verified).
- **Cost-per-lead test:** a scripted standard search must land within the ₹ target; a regression flags a costly change.
- **Idempotency test:** replay a search's provider calls — no double charges.

### E.4 Release process
- **Feature flags:** risky changes ship behind a flag, enabled per workspace first (the operator's own test account), then rolled out.
- **Expand/contract migrations:** no breaking schema change in a single deploy.
- **Rollback plan:** every release has one; images are tagged, DB changes are reversible.
- **Change log + runbook link** in each release. The operator gets a one-line "what changed and what to watch".

### E.5 Go-live checklist (handover to the client)
- [ ] All CI gates green; last nightly eval green; gold-set report attached.
- [ ] Dashboards live: AI calls, Cost, Errors, Provider health, Quality.
- [ ] All alerts wired to the operator's email/Slack and tested (fire one on purpose).
- [ ] Budgets and the platform kill-switch set to the client's numbers.
- [ ] Backups running; a restore drill completed and timed.
- [ ] Secrets in env/KMS, none in the repo (secret-scan clean).
- [ ] Runbooks present: provider outage, SMTP blocklist, cost spike, data-erasure, restore.
- [ ] Load test at target passed; budget caps held.
- [ ] DPDP/GST/compliance items checked (COMPLIANCE.md).

---

## Part F — What an admin sees (summary)

| Console page | Answers the question |
|---|---|
| **AI** | What did the agent calls do, cost, and fail at? |
| **Cost** | What are we spending, per what, and are we still profitable per lead? |
| **Errors** | What broke, in which release, for which customer? |
| **Provider health** | Which data source is worth its cost right now? |
| **Quality** | Are delivered leads actually good? Is the judge fair? |
| **Search timeline** | Exactly what happened in this one search? |
| **Evals** (CI) | Is the app still as good as last release, before it ships? |

All six operator pages read from tables and counters that already exist in the design — this spec adds the `llm_call` table, the redacted sampled prompt capture, the spend caps/kill-switch, the synthetic canary, and the eval gates; everything else is dashboards over data the pipeline already writes.
