# Sales Navigator: Low-Level Design (LLD)

**Version:** 1.0 · **Date:** 2026-11-10 · **Status:** Baseline for implementation
**Read first:** [HLD.md](HLD.md). Reasoning behind decisions: [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md).

---

## 1. Repository layout

```
sales-navigator/
├── backend/
│   ├── pyproject.toml                 # uv-managed; ruff, mypy --strict, pytest
│   ├── alembic.ini
│   ├── app/
│   │   ├── main.py                    # FastAPI app factory
│   │   ├── core/
│   │   │   ├── config.py              # pydantic-settings, loads .env + config/*.yaml
│   │   │   ├── db.py                  # async engine, session, RLS context
│   │   │   ├── security.py            # argon2, JWT, AES-GCM envelope, HMAC
│   │   │   ├── errors.py              # AppError hierarchy → RFC 7807
│   │   │   ├── events.py              # outbox + Redis streams publisher/consumer
│   │   │   ├── money.py               # Money (paise int), currency
│   │   │   ├── logging.py             # structlog + PII redaction
│   │   │   └── telemetry.py           # OpenTelemetry setup
│   │   ├── modules/
│   │   │   ├── identity/      {api.py, service.py, repo.py, models.py, schemas.py}
│   │   │   ├── billing/       {api.py, service.py, razorpay_client.py, gst.py, quota.py, webhooks.py, models.py, schemas.py}
│   │   │   ├── icp/           {api.py, service.py, crawler.py, seeds.py, prompts/, models.py, schemas.py}
│   │   │   ├── geo/           {service.py, parser.py, resolver.py, tiling.py, loaders/geonames.py, models.py}
│   │   │   ├── planner/       {service.py, features.py, rules.py, ai_tiebreak.py, bandit.py, models.py, schemas.py}
│   │   │   ├── search/        {api.py, orchestrator.py, state.py, sse.py, models.py, schemas.py}
│   │   │   ├── discovery/     {service.py, paths/{b2b_company.py, local_business.py, lookalike.py, signal_led.py, b2b2c.py}}
│   │   │   ├── knowledge/     {service.py, normalize.py, resolution.py, merge.py, models.py}
│   │   │   ├── signals/       {service.py, jobs.py, news.py, tech.py, website_facts.py}
│   │   │   ├── scoring/       {service.py, rules.py, llm_fit.py}
│   │   │   ├── contacts/      {service.py, personas.py, titles.py, email_patterns.py, phones.py, waterfall.py}
│   │   │   ├── verification/  {service.py, smtp_client.py, phone_check.py, linkedin_check.py}
│   │   │   ├── compliance/    {api.py, service.py, suppression.py, erasure.py, retention.py}
│   │   │   ├── quality/       {service.py, field_checks.py, row_checks.py, reasoning_judge.py, calibration.py, decisions.py, report.py}
│   │   │   ├── leads/         {api.py, service.py, models.py, schemas.py}
│   │   │   ├── export/        {api.py, service.py, formats/{csv.py, xlsx.py}, crm/{hubspot.py, zoho.py, gsheets.py}, webhooks.py}
│   │   │   ├── providers/     {base.py, gateway.py, ratelimit.py, breaker.py, cache.py, ledger.py, registry.py, licences.yaml,
│   │   │   │                   adapters/{brave.py, overture.py, mca.py, companies_house.py, sec_edgar.py, gleif.py,
│   │   │   │                             jobboards.py, gdelt.py, website.py, claude.py,
│   │   │   │                             plugins/{apollo.py, pdl.py, hunter.py, prospeo.py, lusha.py, zerobounce.py, twilio.py, google_places.py}}}
│   │   │   ├── notify/        {service.py, templates/}
│   │   │   └── ops/           {api.py, audit.py, flags.py}
│   │   └── workers/
│   │       ├── celery_app.py          # queues, routing, priorities
│   │       ├── tasks/                 # thin task wrappers → module services
│   │       └── schedules.py           # beat schedule
│   ├── migrations/
│   └── tests/{unit, contract/cassettes, integration, eval/{planner, fit, judge}, e2e}
├── smtp_verifier/                     # separate deployable
│   ├── app.py, smtp_probe.py, throttle.py, Dockerfile
├── frontend/                          # Next.js app (app router)
├── config/
│   ├── plans.yaml  planner_rules.yaml  waterfall.yaml  quality.yaml  scoring_weights.yaml
│   ├── region_groups.yaml  geo_aliases.yaml  title_taxonomy.yaml  place_categories.yaml
├── deploy/{docker-compose.yml, docker-compose.prod.yml, Caddyfile, wal-g/, runbooks/}
└── docs/{SYSTEM_DESIGN.md, HLD.md, LLD.md, INSTALL.md, ADMIN.md, ADD_A_PROVIDER.md, API.md, COMPLIANCE.md}
```

### 1.1 Layering rules (enforced with import-linter)
```
api.py  →  service.py  →  repo.py / models.py
                 ↘ other modules' service.py only (never their repo/models)
workers/tasks → module service.py
providers.gateway is the ONLY way to reach the network (except smtp_client → smtp_verifier)
```

### 1.2 Conventions
- Python 3.12, `async` throughout, type hints required, `mypy --strict`.
- Money is stored as **integer paise** (`Money(amount_paise: int, currency="INR")`). Provider costs are stored in USD micro-cents and converted using `FX_USD_INR` (config, currently 93).
- IDs are UUIDv7 (time-ordered). Timestamps are `timestamptz` in UTC. The UI shows IST.
- Every service method that changes data takes a `ctx: RequestContext` (workspace_id, user_id, role, request_id).

---

## 2. Core domain types (Pydantic v2)

```python
# app/core/types.py
class Provenance(BaseModel):
    source: str                       # "brave", "overture", "mca", "website", "pattern", "plugin:apollo"
    observed_at: datetime
    url: HttpUrl | None = None
    raw_ref: str | None = None        # provider_cache key

class FieldValue(BaseModel, Generic[T]):
    value: T
    confidence: float = Field(ge=0, le=1)
    provenance: list[Provenance]

class GeoScope(BaseModel):
    level: Literal["global", "group", "country", "state", "district", "city", "radius"]
    country_iso2: str | None = None
    admin1_code: str | None = None    # e.g. "IN-MH"
    admin2_code: str | None = None
    city_geoname_id: int | None = None
    lat: float | None = None
    lon: float | None = None
    radius_km: float | None = None
    bbox: tuple[float, float, float, float] | None = None   # minx, miny, maxx, maxy
    label: str                        # "Pune, Maharashtra, India"

class Persona(BaseModel):
    role: str
    seniority: list[Literal["owner","founder","c_suite","vp","head","director","manager","senior","entry"]]
    departments: list[str] = []
    priority: int = 1
    only_if_employees_lt: int | None = None

class ICP(BaseModel):
    version: int
    seller_mode: Literal["b2b", "b2c"]
    industries: list[WeightedTerm]
    target_size: list[Literal["micro","smb","mid","enterprise"]]
    employee_bands: list[str] = []
    technologies_any: list[str] = []
    buyer_personas: list[Persona]
    pains: list[str]
    buying_triggers: list[str]
    exclusions: Exclusions
    segments: list[Segment]           # 2–3 suggested starting segments (P8)
    inferred_from: dict[str, Literal["website","seeds","user"]]

class PathName(StrEnum):
    B2B_COMPANY = "B2B_COMPANY"; LOCAL_BUSINESS = "LOCAL_BUSINESS"; LOOKALIKE = "LOOKALIKE"
    SIGNAL_LED = "SIGNAL_LED"; B2B2C = "B2B2C"; AUDIENCE_INSIGHT = "AUDIENCE_INSIGHT"

class PathPlan(BaseModel):
    path: PathName
    share: float                      # sums to 1.0 across paths
    sources: list[str]
    persona: list[str]
    contact_strategy: list[Literal["work_email","linkedin","business_phone","mobile","whatsapp_business","website_email"]]

class SearchPlan(BaseModel):
    plan_version: int
    paths: list[PathPlan]
    geo_tiles: list[GeoScope]
    expected: ExpectedYield           # candidates, delivered, pass_rate
    estimate: CostEstimate            # brave_queries, llm_tokens, smtp_checks, plugin_calls, cost_paise
    confidence: float
    rationale: list[str]
    question: ClarifyingQuestion | None = None
    decided_by: Literal["rules","ai","user"]

class ContactPointDTO(BaseModel):
    kind: Literal["work_email","personal_email","mobile","direct_dial","business_phone","whatsapp_business","linkedin"]
    value: str
    verify_status: Literal["valid","catch_all","invalid","unknown","risky","mobile","landline","voip","matched","unconfirmed"]
    confidence: float
    sources: list[str]
    verified_at: datetime | None
    dnc_flag: bool = False

class Reason(BaseModel):
    id: str
    text: str
    evidence_ids: list[UUID]

class Decision(StrEnum):
    PASS="pass"; DEGRADE="degrade"; HOLD="hold"; REJECT="reject"
```

---

## 3. Database schema (PostgreSQL 16)

Extensions: `pgcrypto`, `citext`, `vector`, `postgis`, `pg_trgm`.

### 3.1 Identity and tenancy
```sql
CREATE TABLE workspace (
  id uuid PRIMARY KEY, name text NOT NULL, slug citext UNIQUE NOT NULL,
  gstin text, billing_state_code char(2), status text NOT NULL DEFAULT 'active', -- active|grace|readonly|deleted
  settings jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE app_user (
  id uuid PRIMARY KEY, email citext UNIQUE NOT NULL, password_hash text, google_sub text UNIQUE,
  totp_secret_enc bytea, email_verified_at timestamptz, created_at timestamptz DEFAULT now());

CREATE TABLE membership (
  workspace_id uuid REFERENCES workspace(id) ON DELETE CASCADE,
  user_id uuid REFERENCES app_user(id) ON DELETE CASCADE,
  role text NOT NULL CHECK (role IN ('owner','admin','member','viewer')),
  PRIMARY KEY (workspace_id, user_id));

CREATE TABLE refresh_token (
  id uuid PRIMARY KEY, user_id uuid NOT NULL, family_id uuid NOT NULL, token_hash bytea NOT NULL,
  expires_at timestamptz NOT NULL, revoked_at timestamptz, replaced_by uuid);
```

### 3.2 Row-level security pattern (every tenant table)
```sql
ALTER TABLE search ENABLE ROW LEVEL SECURITY;
ALTER TABLE search FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON search
  USING (workspace_id = current_setting('app.workspace_id', true)::uuid)
  WITH CHECK (workspace_id = current_setting('app.workspace_id', true)::uuid);
-- The app connects as role `app_rw` (subject to RLS). Workers that touch the global store or
-- run cross-tenant maintenance use role `app_system` (BYPASSRLS), only from maintenance tasks.
```
```python
# core/db.py
@asynccontextmanager
async def tenant_session(workspace_id: UUID) -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as s, s.begin():
        await s.execute(text("SELECT set_config('app.workspace_id', :w, true)"), {"w": str(workspace_id)})
        yield s
```

### 3.3 ICP, search, plan
```sql
CREATE TABLE icp (
  id uuid PRIMARY KEY, workspace_id uuid NOT NULL, version int NOT NULL, status text NOT NULL, -- draft|active|archived
  body jsonb NOT NULL, created_by uuid, created_at timestamptz DEFAULT now(),
  UNIQUE (workspace_id, id, version));

CREATE TABLE seed_customer (
  id uuid PRIMARY KEY, workspace_id uuid NOT NULL, icp_id uuid NOT NULL,
  company_id uuid REFERENCES company(id), input_text text NOT NULL, kind text NOT NULL); -- seed|exclude

CREATE TABLE search (
  id uuid PRIMARY KEY, workspace_id uuid NOT NULL, icp_id uuid NOT NULL, icp_version int NOT NULL,
  region_text text NOT NULL, geoscope jsonb NOT NULL,
  status text NOT NULL,  -- DRAFT|PLANNING|NEEDS_INPUT|ESTIMATED|RUNNING|PAUSED_BUDGET|JUDGING|COMPLETED|FAILED|CANCELLED
  stage text,            -- current pipeline stage while RUNNING
  target_count int NOT NULL, delivered_count int NOT NULL DEFAULT 0,
  budget_paise bigint NOT NULL, spent_paise bigint NOT NULL DEFAULT 0,
  quota_reservation_id uuid, checkpoint jsonb NOT NULL DEFAULT '{}',
  created_by uuid, created_at timestamptz DEFAULT now(), completed_at timestamptz);
CREATE INDEX ON search (workspace_id, created_at DESC);

CREATE TABLE search_plan (
  id uuid PRIMARY KEY, workspace_id uuid NOT NULL, search_id uuid NOT NULL REFERENCES search(id),
  version int NOT NULL, body jsonb NOT NULL, confidence real NOT NULL,
  decided_by text NOT NULL, created_at timestamptz DEFAULT now(), UNIQUE (search_id, version));

CREATE TABLE path_yield (  -- planner learning; workspace_id NULL = global prior
  id uuid PRIMARY KEY, workspace_id uuid, industry_key text NOT NULL, region_key text NOT NULL, path text NOT NULL,
  alpha real NOT NULL DEFAULT 1, beta real NOT NULL DEFAULT 1,       -- Beta posterior of PASS rate
  cost_per_pass_paise real, updated_at timestamptz DEFAULT now(),
  UNIQUE NULLS NOT DISTINCT (workspace_id, industry_key, region_key, path));   -- PG15+: NULL workspace = one global row
```

### 3.4 Global knowledge store (no RLS, accessed via `knowledge.service`)
```sql
CREATE TABLE company (
  id uuid PRIMARY KEY, domain citext UNIQUE,              -- registrable domain, nullable for no-website SMBs
  name text NOT NULL, name_norm text NOT NULL,
  industry_code text, industry_label text, size_band text, employees_est int,
  country_iso2 char(2), admin1_code text, city_geoname_id int, geom geography(Point,4326),
  overture_id text UNIQUE, cin text UNIQUE, lei text UNIQUE,   -- external keys (MCA CIN, GLEIF LEI)
  description text, embedding vector(1024),
  confidence real NOT NULL DEFAULT 0.5, last_refreshed_at timestamptz, created_at timestamptz DEFAULT now());
CREATE INDEX company_name_trgm ON company USING gin (name_norm gin_trgm_ops);
CREATE INDEX company_geom ON company USING gist (geom);
CREATE INDEX company_embedding ON company USING ivfflat (embedding vector_cosine_ops) WITH (lists = 200);

CREATE TABLE company_field (
  id uuid PRIMARY KEY, company_id uuid NOT NULL REFERENCES company(id) ON DELETE CASCADE,
  field text NOT NULL, value jsonb NOT NULL, source text NOT NULL, confidence real NOT NULL,
  observed_at timestamptz NOT NULL, workspace_id uuid);   -- non-null only for tenant-licensed plug-in data
CREATE INDEX ON company_field (company_id, field);

CREATE TABLE person (
  id uuid PRIMARY KEY, company_id uuid REFERENCES company(id) ON DELETE SET NULL,
  full_name text NOT NULL, name_norm text NOT NULL, title text, title_norm text,
  seniority text, department text, linkedin_slug citext UNIQUE,
  tenure_start date, last_seen_at timestamptz, confidence real NOT NULL DEFAULT 0.5,
  erased boolean NOT NULL DEFAULT false, created_at timestamptz DEFAULT now());
CREATE INDEX ON person (company_id, seniority);
CREATE UNIQUE INDEX person_identity ON person (company_id, name_norm) WHERE linkedin_slug IS NULL;

CREATE TABLE contact_point (
  id uuid PRIMARY KEY, person_id uuid REFERENCES person(id) ON DELETE CASCADE,
  company_id uuid REFERENCES company(id) ON DELETE CASCADE,          -- for business phone / generic email
  kind text NOT NULL, value_enc bytea NOT NULL, value_hmac bytea NOT NULL,   -- encrypted + lookup hash
  verify_status text NOT NULL DEFAULT 'unknown', confidence real NOT NULL,
  sources text[] NOT NULL, verified_at timestamptz, dnc_flag boolean NOT NULL DEFAULT false,
  workspace_id uuid,                                                   -- set when from a tenant's paid key
  created_at timestamptz DEFAULT now(), UNIQUE NULLS NOT DISTINCT (kind, value_hmac, workspace_id));

CREATE TABLE evidence (
  id uuid PRIMARY KEY, company_id uuid NOT NULL REFERENCES company(id) ON DELETE CASCADE,
  kind text NOT NULL,  -- website|job|news|funding|tech|registry|review|place
  url text, title text, snippet text NOT NULL, published_at timestamptz, observed_at timestamptz NOT NULL,
  content_hash bytea NOT NULL, UNIQUE (company_id, content_hash));

CREATE TABLE email_pattern (
  domain citext PRIMARY KEY, pattern text NOT NULL,     -- e.g. "{f}{last}", "{first}.{last}"
  support int NOT NULL, confidence real NOT NULL, is_catch_all boolean, mx_host text,
  checked_at timestamptz NOT NULL);
```

### 3.5 Tenant results
```sql
CREATE TABLE candidate (           -- per search; pre-judge working set
  id uuid PRIMARY KEY, workspace_id uuid NOT NULL, search_id uuid NOT NULL,
  company_id uuid NOT NULL, path text NOT NULL, stage text NOT NULL,
  rule_score real, llm_score real, final_score real, fit text, dropped_reason text,
  UNIQUE (search_id, company_id));

CREATE TABLE lead (
  id uuid PRIMARY KEY, workspace_id uuid NOT NULL, search_id uuid NOT NULL,
  company_id uuid NOT NULL, person_id uuid, path text NOT NULL,
  final_score real NOT NULL, fit text NOT NULL, reasons jsonb NOT NULL, opener text,
  contact_snapshot jsonb NOT NULL,      -- contact points as delivered (masked values resolved at read)
  row_confidence real NOT NULL, decision text NOT NULL, reason_codes text[] NOT NULL DEFAULT '{}',
  billable boolean NOT NULL DEFAULT false, accepted_from_hold boolean NOT NULL DEFAULT false,
  delivered_at timestamptz, status text NOT NULL DEFAULT 'new',  -- new|saved|exported|contacted|won|lost
  UNIQUE (workspace_id, search_id, person_id));
CREATE INDEX ON lead (workspace_id, search_id, decision, final_score DESC);
CREATE INDEX ON lead (workspace_id, person_id, delivered_at);   -- duplicate-delivery check

-- Permanent per-customer delivery ledger: a record (person, or business for local paths) delivered
-- to a workspace is NEVER delivered to that workspace again, across all searches and all time.
-- Keyed by stable identity hashes so re-resolved rows or a different person_id still dedupe.
CREATE TABLE delivered_record (
  id uuid PRIMARY KEY, workspace_id uuid NOT NULL,
  identity_kind text NOT NULL,            -- person_linkedin | person_email | person_namecompany | business_domain | business_phone
  identity_hmac bytea NOT NULL,           -- HMAC(normalized identity value, pepper)
  lead_id uuid NOT NULL, person_id uuid, company_id uuid,
  first_delivered_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, identity_kind, identity_hmac)
);
CREATE INDEX ON delivered_record (workspace_id, company_id);

CREATE TABLE judgment (
  id uuid PRIMARY KEY, workspace_id uuid NOT NULL, lead_id uuid NOT NULL,
  layer text NOT NULL, check_code text NOT NULL, passed boolean NOT NULL,
  detail jsonb, judge_model text, judge_version text, at timestamptz DEFAULT now());

CREATE TABLE quality_report (search_id uuid PRIMARY KEY, workspace_id uuid NOT NULL, body jsonb NOT NULL);
CREATE TABLE feedback (id uuid PRIMARY KEY, workspace_id uuid NOT NULL, lead_id uuid NOT NULL, user_id uuid NOT NULL,
  verdict text NOT NULL, reason text, outcome text, at timestamptz DEFAULT now());
CREATE TABLE lead_list (id uuid PRIMARY KEY, workspace_id uuid NOT NULL, name text NOT NULL);
CREATE TABLE list_item (list_id uuid, lead_id uuid, workspace_id uuid NOT NULL, PRIMARY KEY (list_id, lead_id));
```

### 3.6 Billing
```sql
CREATE TABLE plan (code text PRIMARY KEY, name text, price_paise bigint, monthly_leads int, seats int,
  features jsonb, razorpay_plan_id text, active boolean);
CREATE TABLE subscription (
  id uuid PRIMARY KEY, workspace_id uuid NOT NULL, plan_code text NOT NULL REFERENCES plan(code),
  razorpay_subscription_id text UNIQUE, status text NOT NULL,  -- trialing|active|grace|halted|cancelled
  current_period_start timestamptz, current_period_end timestamptz, cancel_at_period_end boolean DEFAULT false);
CREATE TABLE quota_ledger (            -- append-only; balance = SUM(delta) per (workspace, period, kind)
  id uuid PRIMARY KEY, workspace_id uuid NOT NULL, period_id text NOT NULL,  -- "2026-11"
  kind text NOT NULL,                  -- leads|mobile_credits
  delta int NOT NULL, reason text NOT NULL,  -- grant|reserve|consume|release|refund_bounce|overage|topup
  ref_id uuid, at timestamptz DEFAULT now());
CREATE INDEX ON quota_ledger (workspace_id, period_id, kind);
CREATE TABLE invoice (
  id uuid PRIMARY KEY, workspace_id uuid NOT NULL, number text UNIQUE NOT NULL,  -- "SN/2026-27/000123"
  fy text NOT NULL, taxable_paise bigint, cgst_paise bigint, sgst_paise bigint, igst_paise bigint,
  total_paise bigint, place_of_supply char(2), gstin text, pdf_key text, razorpay_payment_id text, issued_at timestamptz);
CREATE TABLE payment_event (id text PRIMARY KEY, type text, payload jsonb, received_at timestamptz, processed_at timestamptz);
```

### 3.7 Providers, compliance, ops
```sql
CREATE TABLE provider_credential (id uuid PRIMARY KEY, workspace_id uuid, provider text NOT NULL,  -- NULL = platform key
  key_enc bytea NOT NULL, status text, last_checked_at timestamptz, UNIQUE (workspace_id, provider));
CREATE TABLE provider_cache (key bytea PRIMARY KEY, provider text, op text, body jsonb, workspace_id uuid,
  expires_at timestamptz NOT NULL);
CREATE TABLE provider_call (id uuid PRIMARY KEY, workspace_id uuid, search_id uuid, provider text, op text,
  cost_micro_usd bigint NOT NULL, cache_hit boolean, status text, latency_ms int, idempotency_key bytea UNIQUE,
  at timestamptz DEFAULT now());   -- partitioned monthly
CREATE TABLE suppression (id uuid PRIMARY KEY, workspace_id uuid,  -- NULL = global (erasure)
  kind text NOT NULL, value_hmac bytea NOT NULL, reason text, at timestamptz DEFAULT now(),
  UNIQUE NULLS NOT DISTINCT (workspace_id, kind, value_hmac));
CREATE TABLE erasure_request (id uuid PRIMARY KEY, requester_email citext, match jsonb, status text,
  received_at timestamptz, completed_at timestamptz);
CREATE TABLE audit_log (id uuid PRIMARY KEY, workspace_id uuid, user_id uuid, action text, target text,
  meta jsonb, ip inet, at timestamptz DEFAULT now());  -- partitioned monthly
CREATE TABLE outbox (id bigserial PRIMARY KEY, topic text, payload jsonb, created_at timestamptz DEFAULT now(),
  published_at timestamptz);
```

---

## 4. Provider layer

### 4.1 Interfaces
```python
# providers/base.py
class Capability(StrEnum):
    WEB_SEARCH="web_search"; NEWS_SEARCH="news_search"; PLACE_SEARCH="place_search"
    COMPANY_SEARCH="company_search"; COMPANY_LOOKUP="company_lookup"; PEOPLE_SEARCH="people_search"
    EMAIL_FIND="email_find"; EMAIL_VERIFY="email_verify"; PHONE_FIND="phone_find"; PHONE_VERIFY="phone_verify"
    JOBS="jobs"; LLM="llm"; FETCH="fetch"

@dataclass(frozen=True)
class Operation:
    provider: str
    op: str                         # e.g. "web_search"
    params: Mapping[str, Any]       # normalized, JSON-serializable
    workspace_id: UUID | None
    search_id: UUID | None
    cache_ttl: timedelta | None
    tenant_private: bool = False    # plug-in data from a tenant's own key

class ProviderAdapter(Protocol):
    name: ClassVar[str]
    capabilities: ClassVar[set[Capability]]
    licence: ClassVar[LicenceInfo]           # from licences.yaml: commercial_ok, storage_ok, attribution
    async def call(self, op: Operation, client: httpx.AsyncClient, key: str | None) -> RawResult: ...
    def parse(self, op: Operation, raw: RawResult) -> Any: ...
    def cost(self, op: Operation, raw: RawResult | None) -> int: ...  # micro-USD
```

### 4.2 Gateway algorithm
```python
# providers/gateway.py
async def execute(self, op: Operation) -> Parsed:
    adapter = self.registry.get(op.provider)
    key = await self.credentials.resolve(op.provider, op.workspace_id)   # tenant key → platform key → None
    if adapter.requires_key and key is None:
        raise ProviderUnavailable(op.provider, "no_key")
    cache_key = blake2b(f"{op.provider}|{op.op}|{canonical_json(op.params)}|{op.workspace_id if op.tenant_private else ''}")
    if (hit := await self.cache.get(cache_key)) is not None:
        await self.ledger.record(op, cost=0, cache_hit=True)
        return adapter.parse(op, hit)
    if self.breaker.is_open(op.provider):
        raise ProviderUnavailable(op.provider, "circuit_open")
    await self.budget.check(op)                     # search budget + daily platform spend cap
    await self.ratelimit.acquire(op.provider, op.workspace_id)   # global + tenant token buckets
    idem = blake2b(f"{op.search_id}|{cache_key}")   # same search never pays twice
    async with self.ledger.idempotent(idem) as slot:
        if slot.already_done:
            return adapter.parse(op, await self.cache.get(cache_key))
        raw = await retry(adapter.call, op, self.http, key,
                          policy=RetryPolicy(attempts=3, base=0.5, cap=8, jitter=True,
                                             retry_on=(429, 500, 502, 503, 504, httpx.TransportError)))
        self.breaker.record(op.provider, ok=raw.ok)
        cost = adapter.cost(op, raw)
        if raw.ok and op.cache_ttl and adapter.licence.storage_ok:
            await self.cache.set(cache_key, raw.body, op.cache_ttl, workspace_id=op.workspace_id if op.tenant_private else None)
        await slot.commit(cost=cost, status=raw.status, latency_ms=raw.latency_ms)
    return adapter.parse(op, raw)
```

- **Rate limiter:** a Redis token bucket implemented as an atomic Lua script. Keys are `rl:{provider}` (global capacity from config) and `rl:{provider}:{ws}` (tenant share). `acquire` waits up to 30 s, then raises `RateLimited`, which the task layer re-queues with a delay.
- **Circuit breaker:** a rolling 60 s window. It **opens** when there are ≥ 20 calls and an error rate ≥ 50%, stays open for 30 s, then goes **half-open** and lets 3 probe calls through.
- **Budget check:** `spent_paise + estimated_cost ≤ budget_paise`, otherwise `BudgetExhausted`, and the orchestrator moves to `PAUSED_BUDGET`.

### 4.3 Adapter specs

| Adapter | Endpoint / method | Key ops | Cache TTL | Notes |
|---|---|---|---|---|
| `brave` | `GET https://api.search.brave.com/res/v1/web/search` and `/res/v1/news/search`, header `X-Subscription-Token` | `web_search(q, country, search_lang, count≤20, offset≤9, freshness)` | 14 d (web), 1 d (news) | **Storage only if `licence.storage_ok`** (storage-rights plan). Cost $0.005 per request |
| `overture` | DuckDB `httpfs` + `spatial` over `s3://overturemaps-us-west-2/release/{ver}/theme=places/type=place/*` | `place_search(bbox, categories[])` | 30 d (per tile+category) | Filter on the `bbox` struct columns first (row-group pruning), then `categories.primary IN (...)` and `confidence ≥ 0.6` |
| `mca` | Monthly bulk import of the data.gov.in company master CSVs → `company` (CIN, name, state, class, status, incorporation date) | `company_lookup(name, state)` | n/a (imported) | Only `status = Active`. Matched to domains later |
| `companies_house` | `api.company-information.service.gov.uk` `/search/companies`, `/company/{n}/officers` (basic auth) | `company_search`, `officers` | 30 d | UK only. 600 requests / 5 min |
| `sec_edgar` | `data.sec.gov/submissions/CIK{cik}.json` | `company_lookup` | 30 d | User-Agent header from operator config. ≤ 10 requests/s |
| `gleif` | `api.gleif.org/api/v1/lei-records?filter[...]` | `company_lookup` | 90 d | Global legal entities |
| `jobboards` | Greenhouse `boards-api.greenhouse.io/v1/boards/{t}/jobs`, Lever `api.lever.co/v0/postings/{c}?mode=json`, Ashby `api.ashbyhq.com/posting-api/job-board/{n}`, Workable `apply.workable.com/api/v1/widget/accounts/{s}`, SmartRecruiters `api.smartrecruiters.com/v1/companies/{id}/postings` | `jobs(company)` | 3 d | The ATS is detected from careers-page links |
| `gdelt` | `api.gdeltproject.org/api/v2/doc/doc?query=…&mode=artlist&format=json` | `news(company, timespan)` | 1 d | Free. Rate-limit politely |
| `website` | httpx fetch + `trafilatura` extraction. Playwright only for allow-listed JavaScript-heavy pages | `fetch(url)` | 7 d | robots.txt respected, 1 request/s per host, 2 MB cap, SSRF guard |
| `claude` | Anthropic Python SDK, Messages API with tool-use structured output, prompt caching, Batch API | `llm(task, schema, inputs)` | none (results stored) | Model per task from config. Token budget per search |
| plug-ins | Vendor APIs | per vendor | per vendor | Off unless a key is present. `tenant_private=True` when it's the tenant's key |

### 4.4 Brave query templates (`config/brave_queries.yaml`)
```yaml
company_website:   '"{company_name}" {city} official site'
decision_maker:    'site:linkedin.com/in "{role}" "{company_name}"'
decision_maker_alt: 'site:linkedin.com/in {role_synonyms_or} "{company_name}" {country_name}'
segment_discovery: '{industry_terms} companies in {city_or_state} -jobs -careers'
news_signals:      '"{company_name}" (funding OR raises OR expansion OR launches OR acquires)'
```
**Parsing LinkedIn snippets (no visit to LinkedIn):**
- `title` usually looks like `"{Name} - {Title} - {Company} | LinkedIn"`.
- Regex `^(?P<name>[^-|–]+)\s[-–]\s(?P<title>[^-|–]+)\s[-–]\s(?P<company>[^|]+)\s\|\sLinkedIn`.
- Falls back to the description text.
- The slug is taken from the URL path `/in/{slug}`.
- We store the slug, name, title and company only, plus provenance `brave` with the snippet's date.

### 4.5 SMTP verifier service (`smtp_verifier/`)
```
POST /v1/verify  {emails: [...≤50], timeout_s: 20}   (mTLS; called by verification.smtp_client)
→ [{email, status: valid|invalid|catch_all|unknown|risky, mx, code, reason, checked_at}]
```
**Algorithm for each domain:**
1. MX lookup (dnspython). No MX → A-record fallback. Still none → `invalid` (domain).
2. Pick the lowest-preference MX. Per-MX throttle: ≤ 1 connection and ≤ 20 RCPT per session, ≥ 2 s between sessions (Redis lock).
3. `EHLO {verifier_hostname}` (proper rDNS/PTR set), `MAIL FROM:<probe@{verifier_domain}>` (SPF configured on the verifier domain).
4. **Catch-all probe:** `RCPT TO:<{random 20 chars}@{domain}>`. Response 250 → the domain is catch-all.
5. For each email: `RCPT TO:<email>`:
   - 250/251 → `valid` (or `catch_all` if step 4 returned 250)
   - 550/551/553 → `invalid`
   - 450/451/452 (greylisting) → retry once after 90 s, then `unknown`
   - timeout → `unknown`
6. `RSET`/`QUIT`. **No `DATA`, so no email is ever sent.**
7. Known providers that block probing (e.g. Microsoft 365 or Google Workspace when they always return 250) → `catch_all` with `reason=provider_accept_all`.
- Results are cached per email for 14 days and per domain (`is_catch_all`, MX) for 7 days in `email_pattern`.

---

## 5. Orchestrator (search state machine)

### 5.1 States and transitions (`search/state.py`)
| From | Event | To | Guard / action |
|---|---|---|---|
| DRAFT | `geo_resolved` | PLANNING | GeoScope valid |
| PLANNING | `plan_ready(conf ≥ 0.6)` | ESTIMATED | Save the plan + estimate |
| PLANNING | `plan_ready(conf < 0.6)` | NEEDS_INPUT | Emit `plan.question` |
| NEEDS_INPUT | `clarified` | PLANNING | Merge the answer into the planner features |
| ESTIMATED | `run` | RUNNING | `billing.reserve(target)` succeeds, budget locked |
| RUNNING | `stage_done` | RUNNING | Advance `stage` |
| RUNNING | `budget_exhausted` | PAUSED_BUDGET | Notify |
| PAUSED_BUDGET | `budget_raised` | RUNNING | Re-check the reservation |
| RUNNING | `judge_batch_done` | RUNNING / JUDGING | — |
| JUDGING | `target_met` or `pool_empty` or `budget_out` | COMPLETED | Release unused reservation, write quality report |
| JUDGING | `short_and_budget_left` | RUNNING (stage=discovery, backfill) | `planner.reallocate()` |
| any | `cancel` | CANCELLED | Release reservation |
| any | `fatal_error` | FAILED | Release reservation, alert |

Transitions are a pure function `transition(state, event) -> (new_state, effects[])`. Effects (enqueue a task, publish SSE, call billing) are executed after the DB commit through the **outbox**, so a crash never leaves state and effects out of sync.

### 5.2 Batch pipeline (Celery canvas)
```
discovery(path, tile) ×N  ──chord──▶ resolve(batch of 50)
resolve ─▶ signals(batch) ─▶ score_companies(batch) ─▶ filter(final_score ≥ 60)
        ─▶ people(batch: ≤2 personas/company) ─▶ contacts(batch) ─▶ verify(batch)
        ─▶ compliance(batch) ─▶ reasons(batch) ─▶ judge(batch) ─▶ deliver(batch)
```
- Batches move forward independently (streaming), so the first leads arrive while discovery is still running.
- **Checkpoint:** `search.checkpoint = {path: {tile_idx, offset}, processed_candidate_ids_hash, stage_counters}`, saved after each batch.
- Tasks use `acks_late=True`, `reject_on_worker_lost=True`, and are idempotent per `(search_id, batch_id, stage)`.
- **Stop condition:** a Redis counter `search:{id}:delivered` is checked before each `people` batch. Once `delivered + in_flight_expected ≥ target`, no new batches start (this prevents overspend).

### 5.3 SSE events (`GET /v1/searches/{id}/stream`)
`search.status`, `plan.question`, `search.estimated`, `progress` ({stage, counts}), `lead.delivered` (lead summary), `lead.held`, `search.paused`, `search.completed` ({quality_report}). Implemented with Redis pub/sub channel `sse:search:{id}`. A `Last-Event-ID` replay buffer of 200 events lives in a Redis list.

---

## 6. Module designs

### 6.1 ICP builder (`icp`)
1. **Crawl** (`crawler.py`): BFS from the URL, at most 15 pages and depth 2, priority by path keywords (`/product`, `/pricing`, `/customers`, `/case`, `/about`, `/solutions`, `/industries`). Extract text with trafilatura, ≤ 6k tokens in total after deduplication.
2. **Seeds** (`seeds.py`):
   - Each seed (domain or name) is resolved through `knowledge.resolve_company()`, which enriches via Brave + website + registries.
   - Commonalities are computed: the most common industry, the size distribution, country/state, technologies seen, and buyer titles if known.
3. **LLM** (`claude`, ICP model):
   - Input: website facts, seed commonalities, user description, `config/place_categories.yaml` (so local-business industries map to Overture categories).
   - Output must validate against the `ICP` schema and include `segments` (2–3), each with an `expected_reachability` note.
4. **Versioning:** each edit creates a new version (immutable rows). A search records the `icp_version` it used.

### 6.2 Geo resolver (`geo`)
- **Data load (once, then monthly):** GeoNames `cities5000`, `admin1CodesASCII`, `admin2Codes`, `countryInfo` → `geo_place(id, kind, name, ascii, country, admin1, admin2, lat, lon, population, bbox)`. Plus `geo_alias` (Bombay → Mumbai, Bangalore → Bengaluru, Gurgaon → Gurugram, "NCR" → group) and `geo_group` (DACH, GCC, ASEAN, EU, "Tier-1 India cities").
- **Parser grammar** (`parser.py`, a small hand-written tokenizer):
  ```
  region   := item ("," | "and" | "+" item)*
  item     := "global" | group | place [radius]
  radius   := ("within"|"around"|number) number ("km"|"kilometres"|"miles")
  ```
- **Resolution:** exact alias → exact ASCII name (trigram similarity ≥ 0.85, highest population first). If ambiguous (e.g. "Aurangabad" exists in Maharashtra and Bihar), return choices for the UI picker. Unknown → one external geocode (rate-limited), result cached in `geo_alias`.
- **No re-type dead-end:** a still-unresolved name never returns the user to a blank box indefinitely. The resolve response carries `did_you_mean` (top trigram matches), `parent` (the enclosing district/city/state the user can search instead), and `allow_map_pin: true`. The frontend counts failed attempts per search; after 2, it hides the free-text box and shows only the guaranteed-resolvable options (suggestion, parent area, or map pin + radius). Every unresolved string is written to `geo_unresolved(text, count, last_at)` for the operator to add to `geo_alias`/`geo_place`.
- **Tiling** (`tiling.py`) for LOCAL_BUSINESS:
  - city → its bbox, split into tiles of ~5×5 km for dense metros (population > 1M) and ~15×15 km otherwise
  - state → the districts' bboxes
  - radius → a circle polygon covered by tiles

### 6.3 Search Planner (`planner`)
**Features** (`features.py`):
```python
@dataclass
class PlannerFeatures:
    seller_mode: str; target_size: set[str]; local_ratio: float   # share of ICP industries that map to place categories
    has_seeds: bool; seed_count: int; trigger_strength: float      # 0..1 from buying_triggers detectability
    geo_level: str; geo_place_count: int; budget_paise: int
    enabled: set[str]; plan_code: str; wants_contacts: bool
```
**Rules** (`config/planner_rules.yaml`, evaluated in order, each adds or removes paths with weights):
```yaml
- id: b2c_contacts_to_b2b2c
  when: "seller_mode == 'b2c' and wants_contacts"
  add: {B2B2C: 1.0}
- id: b2c_insight_only
  when: "seller_mode == 'b2c' and not wants_contacts"
  add: {AUDIENCE_INSIGHT: 1.0}
  stop: true
- id: local_primary
  when: "local_ratio >= 0.6 and target_size <= {'micro','smb'}"
  add: {LOCAL_BUSINESS: 0.8}
- id: company_primary
  when: "local_ratio < 0.4 or target_size & {'mid','enterprise'}"
  add: {B2B_COMPANY: 0.8}
- id: mixed
  when: "0.4 <= local_ratio < 0.6"
  add: {LOCAL_BUSINESS: 0.5, B2B_COMPANY: 0.5}
- id: lookalike
  when: "has_seeds and seed_count >= 5"
  add: {LOOKALIKE: 0.3}
- id: signals
  when: "trigger_strength >= 0.6"
  add: {SIGNAL_LED: 0.2}
- id: source_availability
  when: "'overture' not in enabled and 'google_places' not in enabled"
  remove: [LOCAL_BUSINESS]
  reason: "no place source available"
```
The expressions are evaluated with a **safe expression evaluator** (`simpleeval` with a whitelist: comparisons, `in`, set operators). Never `eval`.

**Combining and confidence:**
```python
weights = normalize(sum_of_rule_weights)                  # path → share
top, second = sorted(weights.values(), reverse=True)[:2] + [0]
rule_conf = min(1.0, 0.5 + (top - second))                # clear winner → high confidence
coverage_prior = bandit.expected_pass_rate(industry_key, region_key, paths)  # Beta posteriors
confidence = 0.7 * rule_conf + 0.3 * coverage_prior_confidence
if 0.45 <= confidence < 0.6 or ambiguous_industry(icp):  # e.g. "healthcare", "education"
    choice = ai_tiebreak(icp, geoscope, candidates=top_two)   # structured: {path, share, rationale, question?}
if confidence < 0.6:
    plan.question = build_question(top_two)               # templated: "Do you sell to {A} or {B}?"
```

**Quota split:** `path_target = round(target_count × share)`, with a minimum of 10 per included path. Paths with a share below 0.1 are dropped.

**Estimate** (`CostEstimate`):
- `candidates = path_target / expected_pass_rate(path)`. Priors: B2B 0.35, LOCAL 0.45, LOOKALIKE 0.45, SIGNAL 0.40, updated by the bandit.
- `brave_queries = candidates × q_per_candidate(path)`. B2B ≈ 1.6, LOCAL ≈ 0.6.
- `llm_tokens = candidates × 2.5k (score) + delivered × 1.5k (reasons + judge)`.
- `smtp_checks = people × 1.8`.
- `cost_paise = Σ unit_cost × FX`.

**Adaptive reallocation** (`bandit.py`):
- After each judged batch: `alpha += passes`, `beta += rejects + holds` per `(industry_key, region_key, path)` at both tenant and global level.
- At 20%, 50% and 80% progress, draw a Thompson sample per path and divide the remaining quota in proportion to `sample / cost_per_pass`. No path drops below 10% of the remaining quota until it has 30 judged rows.

### 6.4 Discovery paths (`discovery/paths`)
| Path | Steps |
|---|---|
| B2B_COMPANY | (1) Knowledge store first: `company` rows matching industry + geo + size (free). (2) Brave `segment_discovery` per (industry term × place) → result domains → `knowledge.resolve_company`. (3) MCA rows by state + industry keywords (NIC code map) → Brave `company_website` to find the domain. (4) Plug-in `company_search` if enabled. |
| LOCAL_BUSINESS | For each tile × Overture category: DuckDB query → upsert `company` (overture_id, name, phone, website, geom). Then the website crawl adds email, WhatsApp (`wa.me/` / `api.whatsapp.com/send?phone=` links) and the owner name from the about page. |
| LOOKALIKE | Seed embedding centroid → pgvector kNN over `company.embedding` within the GeoScope (PostGIS `ST_DWithin` or admin filter). Plus Brave `"companies like {seed}"` / competitor pages. |
| SIGNAL_LED | Job boards for known ATS companies in the region. GDELT/Brave news with trigger keywords → company names → resolve. |
| B2B2C | `place_categories.yaml` maps the B2C product to intermediary categories (e.g. organic food → grocery stores, cafes, gyms) → LOCAL_BUSINESS runner + B2B_COMPANY for distributors. |
| AUDIENCE_INSIGHT | Aggregates only: category density per tile, competitor locations, top communities from Brave. Produces a report, no leads. |

Each runner yields `CandidateCompany(company_id, path, source_rank)` into `candidate` (unique per search). The pool is capped at `5 × path_target`. **Early dedupe:** before enrichment, candidates whose company identity is already in `delivered_record` for this workspace are dropped (cheap filter) so the pipeline doesn't spend credits finding contacts for a record the customer already has. The final per-identity guard still runs at delivery for people-level duplicates that only resolve after enrichment.

### 6.5 Entity resolution (`knowledge/resolution.py`)
**Normalization:**
- `domain`: `tldextract` registrable domain, lowercased, `www.` stripped. Free-mail domains (gmail, yahoo, rediffmail…) are never treated as company domains.
- `name_norm`: lowercased, accents and punctuation stripped, legal suffixes removed (`pvt ltd, private limited, llp, ltd, inc, llc, gmbh, opc`), whitespace collapsed.
- `phone`: E.164 via `phonenumbers` (default region = the company's country).

**Blocking keys** (to find candidates for a match): `domain`; `overture_id`; `cin`; `lei`; `(name_norm prefix 6, city_geoname_id)`; `phone_e164`.

**Match score** for pairs within a block:
```
score = 0.45*[domain equal] + 0.25*jaro_winkler(name_norm) + 0.15*[same city or ≤ 1 km]
      + 0.10*[phone equal] + 0.05*[same industry]
merge if score ≥ 0.80;  review-flag if 0.65–0.80 (kept separate, linked as "possible_duplicate")
```

**Field merging** (`merge.py`): for each field, every candidate value is kept in `company_field`. The winning value maximizes `Σ source_weight × freshness_decay(age) × agreement_bonus`.
- **Source weights:** registry 1.0, website 0.9, plug-in 0.85, overture 0.75, brave 0.6, llm_inferred 0.4.
- **Freshness:** `exp(-age_days / half_life)` with half-life 180 d for company fields and 90 d for people.
- **Agreement:** +0.15 per additional source that agrees.
- The winner's `confidence` = its normalized score.

**People:**
- Match key: `linkedin_slug`, otherwise `(company_id, name_norm)`.
- A title change from a newer source updates `title` and sets `tenure_start` to the date it was observed.

### 6.6 Signals (`signals`)
- `website_facts`: LLM extraction (Haiku) of `{offerings[], customer_types[], locations[], team_size_hint, tech_mentions[]}` from the crawled text. Each fact becomes `evidence(kind=website)`.
- `jobs`: open roles → evidence `job` with title, date and location. Roles are matched to ICP triggers by keyword and embedding similarity.
- `news`: GDELT + Brave news from the last 365 days → evidence `news/funding`. Funding is detected by regex + LLM confirmation.
- `tech`: our own fingerprint rules (`config/tech_fingerprints.yaml`, written in-house to avoid licence issues) applied to HTML/headers/scripts → evidence `tech`.

### 6.7 Scoring (`scoring`)
**Rule score (0–60)** with `config/scoring_weights.yaml`:
```yaml
industry_match: 18       # weighted by ICP industry weight; partial via taxonomy distance
size_match: 10
geo_precision: 6         # inside scope = 6, adjacent tile = 3
tech_match: 6
persona_available: 8     # a person matching a persona exists in the knowledge store
trigger_signals: 8       # recent trigger evidence, decays over 180 days
website_quality: 4       # live site, recent content
```
**LLM fit (0–40)** (`llm_fit.py`, Sonnet):
- Input: ICP (cached prompt prefix) + the company card + the top 12 evidence items (id, kind, date, snippet ≤ 300 characters).
- Tool schema: `{fit: strong|medium|weak|no, score_0_40: int, reasons: [{text ≤ 200 chars, evidence_ids: [≥1]}] (3–5), risks: [str]}`.
- Batched in groups of 5 companies per call to save tokens.

`final = rule + llm`. `fit = no` → drop. A company moves on to people search if `final ≥ 60`.

### 6.8 Contacts (`contacts`)
**Personas** (`personas.py` + `config/title_taxonomy.yaml`):
- Each persona expands into role synonyms (e.g. "Head of Operations" → COO, VP Operations, Operations Manager, Head – Ops).
- A title classifier (rules + keyword dictionary, LLM fallback) outputs `seniority` and `department`.
- **Selection:** for each company, search the knowledge store first, then Brave `decision_maker` queries in persona priority order. Stop at K = 2 matched people. For companies with fewer than 50 employees and a founder/owner persona, the founder is preferred.

**Email pattern engine** (`email_patterns.py`):
```python
PATTERNS = ["{first}.{last}", "{first}", "{f}{last}", "{first}{last}", "{first}_{last}",
            "{f}.{last}", "{first}{l}", "{last}.{first}", "{last}{f}", "{first}-{last}"]
def learn(domain) -> PatternModel:
    # observed = known (name, email) pairs on this domain from websites, press pages, verified history
    counts = Counter(match_pattern(name, email) for name, email in observed(domain))
    if counts: return PatternModel(best=counts.most_common(1)[0][0], support=sum(counts.values()))
    return PatternModel(prior=COUNTRY_PRIORS[country])   # e.g. IN: first.last 0.34, first 0.22, flast 0.14 ...
def candidates(person, model, k=3) -> list[str]:
    # transliterate (unidecode), drop honorifics (Dr, CA, Er), handle single-name people (first only)
    ...
```
**Flow:**
1. Top-k candidates → SMTP verify in one batch per domain.
2. The first `valid` result wins, and that pattern is reinforced in `email_pattern`.
3. If the domain is catch-all → keep the top candidate with `verify_status=catch_all` and confidence = pattern confidence × 0.6.
4. Otherwise → waterfall plug-ins (if enabled and `final ≥ 70`).

**Phones** (`phones.py`):
- **Sources:** Overture phone, website (tel: links, contact page), Brave snippets, plug-ins.
- **Normalization:** E.164. The type comes from `phonenumbers.number_type`.
- **WhatsApp Business:** flagged when the website links to `wa.me/{number}`.
- **Mobile lookups** (plug-in) only for leads with `final ≥ 70` that have mobile credits left in the workspace.

### 6.9 Verification (`verification`)
| Check | Implementation | Writes |
|---|---|---|
| Email | `smtp_client.verify_many()` → verifier service | `contact_point.verify_status/verified_at` |
| Phone | `phonenumbers` validity + type + country match. Twilio plug-in if enabled | `verify_status ∈ {mobile, landline, voip, invalid}` |
| LinkedIn | slug-to-name similarity (rapidfuzz `token_set_ratio ≥ 80`) + company match between the snippet and the knowledge store | `matched/unconfirmed` |
| Freshness | Beat job re-checks emails older than 14 d that are in saved lists, before export | — |

### 6.10 Compliance gate (`compliance`)
- `suppression.check(kinds, values)` → HMAC-SHA256(value_norm, pepper) looked up in both the tenant and global suppression lists. It runs **before** contacts (company domain) and **after** contacts (email/phone).
- **DNC:** phones are flagged `dnc_flag=null → "check required"` by default. If the workspace has uploaded an NCPR-scrubbed list, numbers not on that list are marked as cleared.
- **Erasure** (`erasure.py`):
  1. Match by email/phone/LinkedIn.
  2. Set `person.erased=true` and delete their contact points.
  3. Rewrite `lead.contact_snapshot` to "[erased]".
  4. Add to global suppression and write an audit log entry.
  5. Notify the workspaces involved.
- **Retention** (beat, nightly): purge contact points that are undelivered and older than 180 d, evidence older than 365 d, and expired `provider_cache`.

### 6.11 Reasons and opener
`reasons` come from scoring (§6.7). The opener (Sonnet) gets the person's role + 1 chosen evidence item and produces 1–2 sentences, at most 280 characters, citing that evidence item. If no evidence qualifies, the opener is left empty.

### 6.12 Quality Judge (`quality`)
**Check registry:**
```python
class Check(Protocol):
    code: str; layer: Literal["field","row","reasoning"]; hard: bool
    def run(self, row: JudgeInput) -> CheckResult: ...   # passed, score 0..1, detail, field_to_drop?

FIELD_CHECKS = [EmailStatus(), EmailDomainMatchesCompany(), EmailNameConsistency(), RoleAccount(),
                EmailFreshness(), PhoneParse(), PhoneCountryMatch(), PhoneTypeAllowed(), LinkedInNameMatch()]
ROW_CHECKS   = [CompanyAlive(), GeoInScope(), NotExcluded(), NotSuppressed(), PersonaMatch(),
                RecentJobChange(), CoreReachable(), CrossSourceAgreement(), NotDeliveredToWorkspace()]
```
**Reasoning judge** (`reasoning_judge.py`):
- Uses the judge model, **which must be different from the generator model** (checked at startup).
- Input: ICP summary + reasons + the full text of the cited evidence.
- Output schema: SYSTEM_DESIGN §5.14.
- Unsupported reasons are removed. Fewer than 2 supported reasons remaining → `HOLD` (code `WEAK_REASONING`).

**Aggregation and decision:**
```python
def decide(r: JudgeRun, cfg: QualityConfig, cal: Calibrator) -> tuple[Decision, float, list[str]]:
    if any(c.hard and not c.passed for c in r.row_checks):
        return Decision.REJECT, 0.0, r.failed_codes()
    raw = (cfg.w.company * r.company_conf + cfg.w.person * r.person_conf
           + cfg.w.contact * r.best_contact_conf + cfg.w.reasoning * r.support_ratio
           + cfg.w.freshness * r.freshness)
    conf = cal.predict(raw)                       # isotonic regression, piecewise-linear stored as JSON
    if r.judge_disagrees or r.recent_job_change or r.support_count < 2:
        return Decision.HOLD, conf, r.flag_codes()
    if conf >= cfg.pass_threshold:                # 0.80
        return (Decision.DEGRADE if r.dropped_fields else Decision.PASS), conf, r.flag_codes()
    if conf >= cfg.hold_threshold:                # 0.60
        return Decision.HOLD, conf, r.flag_codes()
    return Decision.REJECT, conf, r.failed_codes()
```
**HOLD adjudication** (`adjudication.py`, opt-in — PROMPT_CACHING_AND_GUARDRAILS §5.2b): for a budgeted sample of HOLD rows, re-run deterministic verifications (SMTP/phone/employer) and an independent equal-or-stronger model's reason check; feed the new signals into `calibrate()` and promote to PASS only if the recomputed confidence ≥ 0.80. A weaker fallback model is never used here. Records before/after confidence and what was re-verified in `judgment`.

**Calibration** (`calibration.py`):
- Weekly beat job.
- Labels come from the gold set + feedback (👍 = 1, 👎 with a data reason = 0) + bounces (0) + delivered-without-bounce after 30 d (weak 1).
- Fits isotonic regression per mode (B2B / LOCAL).
- Only replaces the active model if the ECE (calibration error) improves and there are ≥ 300 labels.

**No duplicate records per customer (`NotDeliveredToWorkspace`, hard check):**
- A record delivered to a workspace is **never delivered to that workspace again — across all searches, permanently** (not a 90-day window). This is a hard row check: a hit → `REJECT(code=duplicate_delivered)`, so it is dropped and the search backfills a fresh lead in its place.
- **Identity, not row id:** dedupe is keyed on stable identity hashes in `delivered_record`, so it holds even if entity resolution produced a new `person_id` or the person changed company/email. A candidate is a duplicate if **any** of its identities already exists for the workspace:
  - person: LinkedIn slug · verified work-email HMAC · `(name_norm, company_domain)` HMAC
  - local-business paths (no named person): business domain HMAC · business phone E.164 HMAC
- **Within the same search** the in-memory `seen` set catches duplicates before a DB round-trip (two paths or tiles returning the same company/person).
- **Deliberate re-pull:** if a user genuinely wants an old lead again, re-delivery only happens through an explicit "include previously delivered" toggle on that search (off by default, audit-logged); it never happens silently.

**Delivery** (`deliver(batch)`):
- In one transaction: create `lead` rows, **insert into `delivered_record` for every identity of each delivered row** (the `UNIQUE (workspace_id, identity_kind, identity_hmac)` constraint is the final guard — an insert conflict means a concurrent search already delivered it, so the row is dropped and backfilled), set `billable` for PASS/DEGRADE, and `billing.consume(reservation, n)`.
- Publish SSE events.
- Increment the Redis counter `delivered`.

### 6.13 Billing (`billing`)
**`config/plans.yaml`:**
```yaml
trial:      {price_paise: 0,       leads: 25,   seats: 1,  days: 14, features: [csv]}
starter:    {price_paise: 199900,  leads: 150,  seats: 1,  features: [csv, xlsx], regions_max: 3}
growth:     {price_paise: 599900,  leads: 600,  seats: 3,  features: [csv, xlsx, sheets, hubspot, zoho, signals]}
pro:        {price_paise: 1499900, leads: 1500, seats: 10, features: [all, api, priority]}
overage_paise_per_lead: 900
mobile_credit_paise: 6000
annual_discount_months: 2
gst_rate: 0.18
```
**Quota operations** (`quota.py`). All are appends to `quota_ledger` inside a `SELECT … FOR UPDATE` on the subscription row:
- `grant(period, plan.leads)` on activation and renewal.
- `reserve(search_id, n)`: balance check → `delta=-n, reason=reserve`. If the balance is insufficient → offer overage (if the workspace enabled it) or return 402.
- `consume(reservation, k)`: records consumption (`delta=0` with reason `consume`, for reporting; the reservation already holds the units).
- `release(reservation, unused)`: `delta=+unused`.
- `refund_bounce(lead)`: `delta=+1` if the bounce is within 30 d and the lead was billable.

Balance = `SUM(delta)`, cached in Redis and recomputed on any doubt.

**Razorpay** (`razorpay_client.py`, `webhooks.py`):
- Create a customer (name, email, GSTIN), then create a subscription (`plan_id`, `total_count=120`, `customer_notify=1`).
- **Webhook** `POST /v1/billing/webhooks/razorpay`:
  - Verify `X-Razorpay-Signature` (HMAC-SHA256 of the raw body with the webhook secret).
  - Insert into `payment_event` by event id (idempotent).
  - Handle `subscription.activated|charged|pending|halted|cancelled`, `payment.failed`.
- **Dunning:** `pending` → grace for 7 days (banner, searches allowed). `halted` → workspace `readonly` (view and export of existing leads only).
- **Daily reconciliation** compares local subscriptions with the Razorpay API.

**GST invoice** (`gst.py`):
- Number: `SN/{FY}/{seq:06d}`, with a separate Postgres sequence per FY.
- `place_of_supply = workspace.billing_state_code`. If it equals the operator's state → CGST 9% + SGST 9%, otherwise IGST 18%.
- PDF rendered with WeasyPrint → object storage → emailed.

### 6.14 Export (`export`)
- **Columns (CSV/XLSX):**
  `company_name, domain, industry, size_band, city, state, country, person_name, title, seniority, linkedin_url, work_email, email_status, phone, phone_type, whatsapp, dnc_check, fit, score, confidence, decision, reason_1..3, evidence_url_1..3, opener, sources, verified_at`
- Before export: emails older than 14 days are re-checked (counted against the workspace budget, at our own SMTP cost only).
- **HubSpot / Zoho:** OAuth app. Upsert company by domain and contact by email, map to a custom property `sn_fit_score`, `sn_reasons`.
- **Google Sheets:** OAuth with the `drive.file` scope. Creates a new sheet per export.
- **Webhooks:** `POST` JSON with an `X-SN-Signature: sha256=` HMAC. Retries with backoff for 24 h. Delivery log kept.

### 6.15 Identity (`identity`)
- Sign-up → verification email (signed token, 24 h) → create workspace + owner membership + trial subscription.
- Login → access JWT (`sub`, `ws`, `role`, `exp` 15 m, EdDSA keys) + refresh cookie (rotating, reuse → revoke the family).
- `get_ctx` dependency: decodes the JWT → loads the membership → `RequestContext` → `tenant_session`.
- **API keys** (Pro+): `sn_live_{prefix}_{secret}`. Only an argon2 hash is stored. Scopes: `searches:write`, `leads:read`.

---

## 7. API specification (v1)

**Common rules:**
- JSON only. Errors follow **RFC 7807**: `{type, title, status, detail, code, request_id}`.
- **Pagination** is cursor-based: `?limit=50&cursor=…` → `{items, next_cursor}`.
- **Idempotency:** a `Idempotency-Key` header on `POST` creates (stored for 24 h).
- **Rate limits:** 120 requests/min per user, 600/min per workspace API key. Headers `RateLimit-*`.

| Method | Path | Request | Response | Errors |
|---|---|---|---|---|
| POST | `/v1/auth/signup` | `{email, password, name, company}` | 201 | 409 `email_taken` |
| POST | `/v1/auth/login` | `{email, password, totp?}` | `{access_token}` + cookie | 401, 423 `locked` |
| POST | `/v1/auth/refresh` | cookie | `{access_token}` | 401 |
| POST | `/v1/icp/draft` | `{website_url?, description?, seeds?: [str], exclusions?: [str]}` | 202 `{icp_id}` | 422 |
| GET | `/v1/icp/{id}` | — | `ICP` | 404 |
| PUT | `/v1/icp/{id}` | partial `ICP` | `ICP` (new version) | 409 `stale_version` |
| POST | `/v1/geo/resolve` | `{text}` | `{scopes: [GeoScope], ambiguities: [{text, options[]}]}` | 422 `unresolvable` |
| POST | `/v1/searches` | `{icp_id, region_text \| geoscopes, target_count (10–1000), budget_paise?, segment_id?}` | 201 `Search` (status → PLANNING) | 402 `quota_exceeded` |
| GET | `/v1/searches/{id}` | — | `Search` + `estimate` | — |
| GET | `/v1/searches/{id}/plan` | — | `{plain_language, plan: SearchPlan}` | — |
| PUT | `/v1/searches/{id}/plan` | `{paths: [{path, share}]}` | `SearchPlan` (decided_by=user) + new estimate | 409 if RUNNING |
| POST | `/v1/searches/{id}/clarify` | `{answer_option_id}` | `Search` | 409 |
| POST | `/v1/searches/{id}/run` | — | 202 | 402, 409 |
| POST | `/v1/searches/{id}/cancel` | — | 202 | — |
| GET | `/v1/searches/{id}/stream` | SSE | events (§5.3) | — |
| GET | `/v1/searches/{id}/quality-report` | — | `QualityReport` | — |
| GET | `/v1/leads` | `search_id, decision, min_score, verified_only, has_phone, cursor` | page of `LeadView` | — |
| GET | `/v1/leads/{id}` | — | `LeadDetail` (score breakdown, reasons + evidence, contacts) | — |
| POST | `/v1/leads/{id}/accept` | — | `Lead` (HOLD → accepted, billable) | 402, 409 |
| POST | `/v1/leads/{id}/feedback` | `{verdict: up\|down, reason?, outcome?}` | 204 | — |
| POST | `/v1/leads/{id}/bounce` | `{email}` | 204 (credit-back if eligible) | — |
| POST | `/v1/lists` / `/v1/lists/{id}/items` | — | — | — |
| POST | `/v1/exports` | `{lead_ids? \| search_id?, format: csv\|xlsx\|sheets\|hubspot\|zoho\|webhook}` | 202 `{export_id}` | 403 plan feature |
| GET | `/v1/exports/{id}` | — | `{status, download_url?}` | — |
| GET | `/v1/billing/plans` / `/v1/billing/subscription` / `/v1/billing/usage` | — | — | — |
| POST | `/v1/billing/subscribe` | `{plan_code, billing_cycle, gstin?, state_code}` | `{razorpay_subscription_id, short_url}` | — |
| POST | `/v1/billing/webhooks/razorpay` | raw | 200 | 400 bad signature |
| POST | `/v1/compliance/suppress` | `{kind, values[]}` or CSV upload | 202 | — |
| POST | `/v1/compliance/erasure` (public, captcha-free; email OTP confirms identity) | `{email}` | 202 | — |
| GET/PUT | `/v1/admin/providers` | keys (masked), waterfall order, test | — | 403 |
| GET | `/v1/admin/providers/health` | — | per-provider metrics | — |
| GET | `/v1/ops/*` | operator console (platform_admin) | — | 403 |

---

## 8. Configuration files

### 8.1 `config/waterfall.yaml`
```yaml
work_email:
  steps: [knowledge_store, pattern_engine, plugin:prospeo, plugin:hunter, plugin:apollo]
  plugin_min_score: 70
  verify_with: smtp_verifier          # or plugin:zerobounce
  accept: [valid]
  keep_labelled: [catch_all]
mobile:
  steps: [knowledge_store, plugin:lusha, plugin:apollo]
  min_score: 70
  requires_credit: mobile_credits
business_phone:
  steps: [overture, website, brave_snippet, plugin:google_places]
linkedin:
  steps: [knowledge_store, brave, plugin:apollo]
```

### 8.2 `config/quality.yaml`
```yaml
pass_threshold: 0.80
hold_threshold: 0.60
weights: {company: 0.25, person: 0.20, contact: 0.30, reasoning: 0.15, freshness: 0.10}
never_redeliver_to_workspace: true     # permanent per-customer dedupe (no time window)
redeliver_requires_explicit_optin: true
email_recheck_days: 14
recent_job_change_days: 90
role_accounts_allowed: false          # info@, sales@ (LOCAL_BUSINESS overrides to true)
judge_model: claude-opus-5-5
generator_model: claude-sonnet-5-5    # must differ from judge_model
adjudication:                         # safe HOLD re-examination (PROMPT_CACHING_AND_GUARDRAILS §5.2b)
  enabled: false                      # opt-in per workspace; default = HOLDs wait for human review (D8)
  model: claude-opus-5-5              # must be >= generator tier; a weaker model may NEVER raise confidence
  requires_new_verification: true     # re-run SMTP/phone/employer checks; a mere second opinion can't promote
  promote_only_if_calibrated_gte: 0.80
  max_adjudications_per_search: 25    # budgeted sample (highest-confidence HOLDs first), not every HOLD
fallback_ladder:                      # availability fallback (PROMPT_CACHING_AND_GUARDRAILS §5.2a)
  icp:           [claude-sonnet-5-5, claude-haiku-5-5]
  fit:           [claude-sonnet-5-5, claude-opus-5-5, claude-haiku-5-5]
  judge:         [claude-opus-5-5, claude-sonnet-5-5]   # never below Sonnet; else rows wait in HOLD
  website_facts: [claude-haiku-5-5, claude-sonnet-5-5]
  opener:        [claude-sonnet-5-5, claude-haiku-5-5]
server_fallbacks: default             # handles refusals inside a single call
```

### 8.3 `config/llm.yaml`
```yaml
tasks:
  icp:            {model: claude-sonnet-5-5, max_tokens: 4000}
  website_facts:  {model: claude-haiku-5-5,  max_tokens: 1200}
  fit:            {model: claude-sonnet-5-5, max_tokens: 2500, batch_size: 5}
  opener:         {model: claude-sonnet-5-5, max_tokens: 300}
  judge:          {model: claude-opus-5-5,   max_tokens: 1500}
  planner_tiebreak: {model: claude-sonnet-5-5, max_tokens: 800}
search_token_budget: 400000
use_batch_api_for: [fit, judge]   # when search.mode == "overnight"
prompt_cache: true
```

> **Caching, injection guardrails, structured-output and refusal handling for all AI calls are specified in [PROMPT_CACHING_AND_GUARDRAILS.md](PROMPT_CACHING_AND_GUARDRAILS.md).** In short: a frozen, cached prefix per task (the ICP is cached across a whole search — ~40–55% LLM saving), untrusted web content is fenced as data, operator instructions use the mid-conversation system channel, every call returns schema-validated JSON (invalid/refusal → HOLD, never delivered), and evidence citations are enforced against our own store.

---

## 9. Cache keys and TTLs

| Key | Store | TTL |
|---|---|---|
| `provider_cache[blake2b(provider,op,params,ws?)]` | Postgres + Redis hot copy | Per adapter (§4.3) |
| `rl:{provider}`, `rl:{provider}:{ws}` | Redis | Rolling |
| `brk:{provider}` | Redis | 60 s window |
| `quota:{ws}:{period}:{kind}` | Redis | 10 min (recomputed from the ledger) |
| `search:{id}:delivered`, `search:{id}:inflight` | Redis | Lifetime of search + 1 d |
| `sse:search:{id}` + replay list | Redis | 1 d |
| `mx:{domain}`, `catchall:{domain}` | Redis + `email_pattern` | 7 d |
| `email:{hmac}` verify result | `contact_point` | 14 d |
| `geo:alias:{text}` | Postgres `geo_alias` | Permanent |

---

## 10. Error handling and retries

| Error | Where raised | Handling |
|---|---|---|
| `ProviderUnavailable(no_key \| circuit_open)` | gateway | The path runner skips that source and records `skipped_sources`. The planner may re-estimate |
| `RateLimited` | gateway | Task retried with a 5–60 s countdown (does not count as a failure) |
| `BudgetExhausted` | gateway / orchestrator | Search → `PAUSED_BUDGET`, SSE + email notification |
| `QuotaExceeded` | billing | 402 to the API. Running searches stop delivering and finish as partial |
| `LLMSchemaError` | claude adapter | 1 repair retry with the validation error in the prompt, then the row → `HOLD(code=LLM_INVALID)` |
| `CrawlBlocked` (robots/403) | website adapter | Evidence skipped. The company can still pass on other evidence |
| `SMTPUnknown` | verifier | Status `unknown` → the field is removed by the judge (DEGRADE) |
| DB serialization failure | services | Automatic retry ×3 (tenacity) for `SERIALIZABLE` billing transactions |
| Unhandled error | any task | Celery retry ×3 with exponential backoff → search `FAILED` if the orchestrator task fails → Sentry |

---

## 11. Observability details

> Agent-call records (`llm_call`), cost caps/kill-switch, error audit trail, eval gates and the release checklist are specified in [OBSERVABILITY_AND_QA.md](OBSERVABILITY_AND_QA.md).

**Metrics:**
```
sn_http_requests_total{route,method,status}           sn_http_latency_seconds{route}
sn_queue_depth{queue}  sn_queue_oldest_age_seconds{queue}
sn_provider_calls_total{provider,op,status,cache}     sn_provider_cost_micro_usd_total{provider}
sn_provider_latency_seconds{provider,op}
sn_search_stage_seconds{stage}                         sn_search_outcome_total{status}
sn_quality_decisions_total{decision,path}              sn_quality_reason_codes_total{code}
sn_delivered_leads_total{plan}                         sn_quota_consumed_total{plan}
sn_smtp_results_total{status,mx_provider}              sn_llm_tokens_total{task,model,kind=in|out|cached}
```
**Log fields:** `ts, level, msg, request_id, trace_id, workspace_id, user_id, search_id, task, provider, duration_ms`. Redaction filter masks emails and phones (`r***@acme.in`, `+91******3210`).
**Trace spans:** `http.request` → `task.{name}` → `provider.{name}.{op}` → `db.query` (sampled at 10%, errors always kept).

---

## 12. Frontend design (`frontend/`)

| Route | Purpose | Key components |
|---|---|---|
| `/signup`, `/login`, `/verify` | Authentication | AuthForm, GoogleButton |
| `/onboarding` | Website + seeds → ICP draft → segment pick | IcpCard (editable chips), SegmentPicker |
| `/search/new` | Region + segment + target + budget → plan + estimate → run | RegionInput (autocomplete + ambiguity picker + map preview), PlanSummary (plain language, override), EstimateBar (₹, quota impact), ClarifyDialog |
| `/search/[id]` | Live results | ProgressStepper, LeadTable (virtualized), Filters, HoldTab, QualityReportPanel |
| `/leads/[id]` | Lead detail | ScoreBreakdown, ReasonList (evidence links), ContactPoints (badges + sources), OpenerCopy, FeedbackButtons |
| `/lists`, `/exports` | Lists and exports | ExportDialog (format, CRM connect) |
| `/settings/billing` | Plan, usage, invoices | PlanCards, UsageMeter, InvoiceTable, RazorpayCheckout |
| `/settings/team`, `/settings/api` | Users, roles, API keys | — |
| `/admin/providers` | Keys, waterfall drag-order, health | ProviderHealthChart |
| `/ops/*` | Operator console | Tenants, subscriptions, erasure queue, spend, flags |

- **State:** TanStack Query for server state. SSE via `EventSource` with `Last-Event-ID`, updating the query cache.
- **Auth:** the access token is kept in memory and refreshed on 401 via the cookie.
- **Localization:** English first. Prices shown as `₹1,999 + GST`. IST timestamps.
- **Accessibility:** WCAG AA. Keyboard-navigable tables.

---

## 13. Testing (per module)

| Module | Unit focus | Contract / integration | Eval / acceptance |
|---|---|---|---|
| providers | Cache keying, idempotency, breaker, token bucket (fakeredis) | vcrpy recorded responses per adapter. Nightly live test with ₹ cap | — |
| geo | Parser grammar, alias resolution, ambiguity, tiling | GeoNames fixture subset | 300 Indian place strings → ≥ 98% correct |
| planner | Rule evaluation, share normalization, confidence, bandit updates | — | 150 labelled cases → ≥ 90% correct primary path. Every ambiguous case asks a question |
| knowledge | Normalizers, match score, merge winner | Postgres testcontainer | 500 labelled duplicate pairs → F1 ≥ 0.95 |
| contacts | Pattern matching / learning, transliteration, title classifier | Verifier fake server | Indian name set (initials, single names, honorifics) |
| verification | SMTP response mapping | aiosmtpd fake MX (valid / invalid / catch-all / greylist) | — |
| scoring / quality | Weights, decision table, calibration fit | — | Judge: PASS precision ≥ 90%, false-reject ≤ 15%. Fit agreement ≥ 80% |
| billing | Ledger math, GST split, invoice numbering, webhook signature + idempotency | Razorpay test mode | Full subscription lifecycle e2e |
| compliance | HMAC suppression, erasure cascade | — | Erasure leaves no remaining personal data (DB scan test) |
| e2e | — | Playwright: sign-up → ICP → search → leads → export → upgrade | — |

**Gold set** (`tests/eval/gold/`): 200 contacts each for India-B2B, India-local and Global-B2B, verified by hand. Run quarterly. The results are published in the operator console and as a client report.

**Golden end-to-end cases** (`tests/eval/golden/`): fully labelled business→outcome cases (plan, ICP, positives, negatives, injection traps, thresholds) that gate every release — specified in [GOLDEN_TESTCASE.md](GOLDEN_TESTCASE.md), fixture [golden/case_bluecart.json](golden/case_bluecart.json).

---

## 14. Build order (maps to the ~8–10 week plan)

| Week | Deliverables |
|---|---|
| 1 | Repo, CI, core (config, db, RLS, errors, logging), identity, provider gateway + Brave + website + Claude adapters |
| 2 | Geo (GeoNames load, parser, tiling), ICP builder, knowledge store + entity resolution |
| 3 | Planner (rules, estimate, clarify), orchestrator + state machine + SSE, discovery B2B_COMPANY + LOCAL_BUSINESS (Overture) |
| 4 | Signals (website facts, jobs, news), scoring (rules + LLM fit), people finder + title taxonomy |
| 5 | Email pattern engine, SMTP verifier service, phones, waterfall, compliance gate |
| 6 | Quality Judge + calibration + backfill, leads API, quality report |
| 7 | Billing (Razorpay, quota ledger, GST invoices, dunning), exports (CSV/XLSX/Sheets/HubSpot/Zoho/webhooks) |
| 8 | Frontend complete, admin + ops consoles |
| 9 | Hardening: load test, security scan, gold-set run, runbooks, INSTALL/ADMIN docs |
| 10 | Staging soak with pilot customers, fixes, handover |
