-- ===========================================================================
-- 001_schema.sql - Sales Navigator base schema (PostgreSQL 15+)
--
-- Runs as role sn_owner (the schema owner). Read top to bottom:
--   1. Extensions
--   2. GLOBAL tables     - shared public business knowledge + reference data
--   3. TENANT tables     - one customer's private data (row-level security)
--   4. Observability     - cost / AI / error ledgers
--   5. Row-level security policies
--   6. Grants to the runtime roles (sn_app, sn_system)
--
-- Tenant isolation rule: every TENANT table has `workspace_id` and a policy
--   workspace_id = app_workspace_id()
-- where app_workspace_id() reads the per-transaction setting written by
-- app/core/db.py::tenant_tx(). The API role (sn_app) cannot bypass it.
-- ===========================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- fuzzy name matching (geo "did you mean")

-- Helper: the workspace of the current transaction, or NULL if none is set.
-- NULLIF handles the empty string a pooled connection holds after COMMIT.
CREATE OR REPLACE FUNCTION app_workspace_id() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT NULLIF(current_setting('app.workspace_id', true), '')::uuid
$$;

-- ---------------------------------------------------------------------------
-- 2. GLOBAL tables (no workspace_id, no RLS)
-- ---------------------------------------------------------------------------

-- Subscription plans (seeded from config/plans.yaml).
CREATE TABLE plan (
  code           text PRIMARY KEY,
  name           text NOT NULL,
  price_paise    bigint NOT NULL,
  monthly_leads  int NOT NULL,
  seats          int NOT NULL,
  features       jsonb NOT NULL DEFAULT '[]'
);

-- Platform users. A user can belong to several workspaces (see membership).
CREATE TABLE app_user (
  id                 uuid PRIMARY KEY,
  email              text NOT NULL,
  full_name          text NOT NULL,
  password_hash      text NOT NULL,
  is_platform_admin  boolean NOT NULL DEFAULT false,
  created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX app_user_email_uq ON app_user (lower(email));

-- Built-in place table: countries, states, districts, cities (+ aliases).
CREATE TABLE geo_place (
  id            serial PRIMARY KEY,
  kind          text NOT NULL CHECK (kind IN ('country','state','district','city')),
  name          text NOT NULL,
  ascii_name    text NOT NULL,          -- lower-case, accents stripped: used for matching
  country_iso2  char(2) NOT NULL,
  admin1_code   text,                    -- e.g. IN-MH
  parent_name   text,                    -- state for a city, country for a state
  lat           double precision,
  lon           double precision,
  population    int NOT NULL DEFAULT 0,
  aliases       text[] NOT NULL DEFAULT '{}'
);
CREATE INDEX geo_place_ascii_trgm ON geo_place USING gin (ascii_name gin_trgm_ops);
CREATE INDEX geo_place_aliases ON geo_place USING gin (aliases);

-- Region strings nobody could resolve - reviewed by the operator to grow geo_place.
CREATE TABLE geo_unresolved (
  text     text PRIMARY KEY,
  count    int NOT NULL DEFAULT 1,
  last_at  timestamptz NOT NULL DEFAULT now()
);

-- Shared knowledge store: public business information, built once, reused by
-- every tenant (HLD 7.2). No tenant-private data lives here.
CREATE TABLE company (
  id             uuid PRIMARY KEY,
  domain         text UNIQUE,            -- registrable domain; NULL for no-website SMBs
  name           text NOT NULL,
  name_norm      text NOT NULL,
  industry       text,
  size_band      text,                   -- micro | smb | mid | enterprise
  employees      int,
  city           text,
  admin1_code    text,
  country_iso2   char(2),
  tech           text[] NOT NULL DEFAULT '{}',
  description    text,
  alive          boolean NOT NULL DEFAULT true,
  sources        text[] NOT NULL DEFAULT '{}',
  confidence     real NOT NULL DEFAULT 0.5,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX company_name_trgm ON company USING gin (name_norm gin_trgm_ops);
CREATE INDEX company_region ON company (country_iso2, admin1_code);

CREATE TABLE person (
  id             uuid PRIMARY KEY,
  company_id     uuid REFERENCES company(id) ON DELETE SET NULL,
  full_name      text NOT NULL,
  name_norm      text NOT NULL,
  title          text,
  seniority      text,
  department     text,
  linkedin_slug  text UNIQUE,
  confidence     real NOT NULL DEFAULT 0.5,
  erased         boolean NOT NULL DEFAULT false,
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX person_company ON person (company_id);
CREATE UNIQUE INDEX person_identity ON person (company_id, name_norm) WHERE linkedin_slug IS NULL;

-- Contact values are encrypted (value_enc) and looked up by keyed hash (value_hmac).
CREATE TABLE contact_point (
  id             uuid PRIMARY KEY,
  person_id      uuid REFERENCES person(id) ON DELETE CASCADE,
  company_id     uuid REFERENCES company(id) ON DELETE CASCADE,
  kind           text NOT NULL,          -- work_email | business_phone | mobile | whatsapp | linkedin
  value_enc      bytea NOT NULL,
  value_hmac     bytea NOT NULL,
  verify_status  text NOT NULL DEFAULT 'unknown',
  confidence     real NOT NULL DEFAULT 0.5,
  sources        text[] NOT NULL DEFAULT '{}',
  verified_at    timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (kind, value_hmac)
);
CREATE INDEX contact_point_person ON contact_point (person_id);

CREATE TABLE evidence (
  id            uuid PRIMARY KEY,
  company_id    uuid NOT NULL REFERENCES company(id) ON DELETE CASCADE,
  kind          text NOT NULL,           -- website | job | news | tech | registry | place
  url           text,
  snippet       text NOT NULL,
  observed_at   timestamptz NOT NULL DEFAULT now(),
  content_hash  text NOT NULL,
  UNIQUE (company_id, content_hash)
);

-- Learned email format per domain (e.g. "{first}.{last}").
CREATE TABLE email_pattern (
  domain        text PRIMARY KEY,
  pattern       text NOT NULL,
  support       int NOT NULL DEFAULT 1,
  confidence    real NOT NULL,
  is_catch_all  boolean,
  checked_at    timestamptz NOT NULL DEFAULT now()
);

-- Provider response cache (only for sources whose licence allows storage).
CREATE TABLE provider_cache (
  key         text PRIMARY KEY,
  provider    text NOT NULL,
  op          text NOT NULL,
  body        jsonb NOT NULL,
  expires_at  timestamptz NOT NULL
);

-- ---------------------------------------------------------------------------
-- 3. TENANT tables (workspace_id + row-level security)
-- ---------------------------------------------------------------------------
CREATE TABLE workspace (
  id          uuid PRIMARY KEY,
  name        text NOT NULL,
  status      text NOT NULL DEFAULT 'active',   -- active | readonly | deleted
  settings    jsonb NOT NULL DEFAULT '{}',
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE membership (
  workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
  user_id       uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
  role          text NOT NULL CHECK (role IN ('owner','admin','member','viewer')),
  PRIMARY KEY (workspace_id, user_id)
);

CREATE TABLE subscription (
  id              uuid PRIMARY KEY,
  workspace_id    uuid NOT NULL UNIQUE REFERENCES workspace(id) ON DELETE CASCADE,
  plan_code       text NOT NULL REFERENCES plan(code),
  status          text NOT NULL,              -- trialing | active | grace | halted
  current_period  text NOT NULL,              -- 'YYYY-MM'
  started_at      timestamptz NOT NULL DEFAULT now()
);

-- Append-only quota ledger. Balance = SUM(delta) for (workspace, period, kind).
CREATE TABLE quota_ledger (
  id            uuid PRIMARY KEY,
  workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
  period        text NOT NULL,
  kind          text NOT NULL DEFAULT 'leads',
  delta         int NOT NULL,
  reason        text NOT NULL,   -- grant | reserve | release | refund_bounce | adjust
  ref_id        uuid,
  at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX quota_ledger_balance ON quota_ledger (workspace_id, period, kind);

-- Ideal Customer Profile. Edits create a new version row in the same lineage.
CREATE TABLE icp (
  id            uuid PRIMARY KEY,
  workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
  lineage_id    uuid NOT NULL,
  version       int NOT NULL,
  status        text NOT NULL DEFAULT 'active',  -- active | archived
  body          jsonb NOT NULL,
  source        jsonb NOT NULL DEFAULT '{}',     -- website_url, description, seeds
  created_by    uuid,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (lineage_id, version)
);

CREATE TABLE search (
  id                  uuid PRIMARY KEY,
  workspace_id        uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
  icp_id              uuid NOT NULL REFERENCES icp(id),
  region_text         text NOT NULL,
  geoscope            jsonb NOT NULL DEFAULT '{}',
  status              text NOT NULL,
  stage               text,
  target_count        int NOT NULL,
  delivered_count     int NOT NULL DEFAULT 0,
  held_count          int NOT NULL DEFAULT 0,
  rejected_count      int NOT NULL DEFAULT 0,
  budget_paise        bigint NOT NULL,
  spent_paise         bigint NOT NULL DEFAULT 0,
  plan                jsonb,
  estimate            jsonb,
  question            jsonb,
  quota_reserved      int NOT NULL DEFAULT 0,
  include_previously_delivered boolean NOT NULL DEFAULT false,
  quality_report      jsonb,
  error               text,
  created_by          uuid,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  completed_at        timestamptz
);
CREATE INDEX search_ws_created ON search (workspace_id, created_at DESC);

-- Timeline of everything that happened in a search (the operator's debug view).
CREATE TABLE search_event (
  id            uuid PRIMARY KEY,
  workspace_id  uuid NOT NULL,
  search_id     uuid NOT NULL REFERENCES search(id) ON DELETE CASCADE,
  at            timestamptz NOT NULL DEFAULT now(),
  kind          text NOT NULL,     -- state | stage | provider | llm | judge | billing | error
  message       text NOT NULL,
  data          jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX search_event_search ON search_event (search_id, at);

-- Background job queue (worker claims with FOR UPDATE SKIP LOCKED).
CREATE TABLE search_job (
  id            uuid PRIMARY KEY,
  workspace_id  uuid NOT NULL,
  search_id     uuid NOT NULL REFERENCES search(id) ON DELETE CASCADE,
  status        text NOT NULL DEFAULT 'queued',   -- queued | running | done | failed
  run_after     timestamptz NOT NULL DEFAULT now(),
  attempts      int NOT NULL DEFAULT 0,
  locked_at     timestamptz,
  last_error    text,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX search_job_claim ON search_job (status, run_after);

-- Per-search working set of companies before judging.
CREATE TABLE candidate (
  id              uuid PRIMARY KEY,
  workspace_id    uuid NOT NULL,
  search_id       uuid NOT NULL REFERENCES search(id) ON DELETE CASCADE,
  company_id      uuid NOT NULL REFERENCES company(id),
  path            text NOT NULL,
  rule_score      real,
  llm_score       real,
  final_score     real,
  fit             text,
  reasons         jsonb,
  dropped_reason  text,
  UNIQUE (search_id, company_id)
);

-- Rows the Quality Judge decided on (PASS / DEGRADE / HOLD / REJECT).
CREATE TABLE lead (
  id                  uuid PRIMARY KEY,
  workspace_id        uuid NOT NULL,
  search_id           uuid NOT NULL REFERENCES search(id) ON DELETE CASCADE,
  company_id          uuid NOT NULL REFERENCES company(id),
  person_id           uuid REFERENCES person(id),
  path                text NOT NULL,
  final_score         real NOT NULL,
  fit                 text NOT NULL,
  reasons             jsonb NOT NULL DEFAULT '[]',
  opener              text,
  contact_snapshot    jsonb NOT NULL DEFAULT '{}',
  row_confidence      real NOT NULL,
  decision            text NOT NULL CHECK (decision IN ('pass','degrade','hold','reject')),
  reason_codes        text[] NOT NULL DEFAULT '{}',
  billable            boolean NOT NULL DEFAULT false,
  accepted_from_hold  boolean NOT NULL DEFAULT false,
  delivered_at        timestamptz,
  status              text NOT NULL DEFAULT 'new',
  created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX lead_search_decision ON lead (workspace_id, search_id, decision, final_score DESC);

CREATE TABLE judgment (
  id            uuid PRIMARY KEY,
  workspace_id  uuid NOT NULL,
  lead_id       uuid NOT NULL REFERENCES lead(id) ON DELETE CASCADE,
  layer         text NOT NULL,     -- field | row | reasoning | adjudication
  check_code    text NOT NULL,
  passed        boolean NOT NULL,
  detail        jsonb NOT NULL DEFAULT '{}',
  at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX judgment_lead ON judgment (lead_id);

-- Permanent per-customer delivery ledger: a record delivered to a workspace
-- is never delivered to it again (identity-keyed, no time window).
CREATE TABLE delivered_record (
  id                  uuid PRIMARY KEY,
  workspace_id        uuid NOT NULL,
  identity_kind       text NOT NULL,   -- person_linkedin | person_email | person_namecompany | business_domain | business_phone
  identity_hmac       bytea NOT NULL,
  lead_id             uuid NOT NULL,
  person_id           uuid,
  company_id          uuid,
  first_delivered_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, identity_kind, identity_hmac)
);
CREATE INDEX delivered_record_company ON delivered_record (workspace_id, company_id);

CREATE TABLE feedback (
  id            uuid PRIMARY KEY,
  workspace_id  uuid NOT NULL,
  lead_id       uuid NOT NULL REFERENCES lead(id) ON DELETE CASCADE,
  user_id       uuid NOT NULL,
  verdict       text NOT NULL CHECK (verdict IN ('up','down')),
  reason        text,
  at            timestamptz NOT NULL DEFAULT now()
);

-- Suppression: workspace_id NULL = global (erasure requests); otherwise tenant-level.
CREATE TABLE suppression (
  id            uuid PRIMARY KEY,
  workspace_id  uuid,
  kind          text NOT NULL,     -- email | domain | phone
  value_hmac    bytea NOT NULL,
  reason        text,
  at            timestamptz NOT NULL DEFAULT now(),
  UNIQUE NULLS NOT DISTINCT (workspace_id, kind, value_hmac)
);

-- ---------------------------------------------------------------------------
-- 4. Observability ledgers (written by the worker; tenants read their own rows)
-- ---------------------------------------------------------------------------
CREATE TABLE provider_call (
  id              uuid PRIMARY KEY,
  workspace_id    uuid,
  search_id       uuid,
  provider        text NOT NULL,
  op              text NOT NULL,
  cost_micro_usd  bigint NOT NULL DEFAULT 0,
  cache_hit       boolean NOT NULL DEFAULT false,
  status          text NOT NULL,
  latency_ms      int NOT NULL DEFAULT 0,
  at              timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX provider_call_at ON provider_call (at DESC);
CREATE INDEX provider_call_ws ON provider_call (workspace_id, at DESC);

CREATE TABLE llm_call (
  id               uuid PRIMARY KEY,
  workspace_id     uuid,
  search_id        uuid,
  task             text NOT NULL,     -- icp | fit | judge | opener | planner_tiebreak
  model            text NOT NULL,
  prompt_version   text NOT NULL,
  tokens_in        int NOT NULL DEFAULT 0,
  tokens_cached    int NOT NULL DEFAULT 0,
  tokens_out       int NOT NULL DEFAULT 0,
  cost_micro_usd   bigint NOT NULL DEFAULT 0,
  latency_ms       int NOT NULL DEFAULT 0,
  status           text NOT NULL,     -- ok | schema_error | refusal | error | fallback
  retries          int NOT NULL DEFAULT 0,
  fallback_used    boolean NOT NULL DEFAULT false,
  schema_valid     boolean NOT NULL DEFAULT true,
  evidence_drops   int NOT NULL DEFAULT 0,
  at               timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX llm_call_at ON llm_call (at DESC);
CREATE INDEX llm_call_ws ON llm_call (workspace_id, at DESC);

CREATE TABLE error_event (
  id            uuid PRIMARY KEY,
  workspace_id  uuid,
  search_id     uuid,
  request_id    text,
  location      text NOT NULL,
  error_type    text NOT NULL,
  message       text NOT NULL,
  at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX error_event_at ON error_event (at DESC);

CREATE TABLE audit_log (
  id            uuid PRIMARY KEY,
  workspace_id  uuid,
  user_id       uuid,
  action        text NOT NULL,
  target        text,
  meta          jsonb NOT NULL DEFAULT '{}',
  at            timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 5. Row-level security
-- ---------------------------------------------------------------------------
ALTER TABLE workspace ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON workspace
  USING (id = app_workspace_id()) WITH CHECK (id = app_workspace_id());

-- Same policy for every table that carries workspace_id.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'membership','subscription','quota_ledger','icp','search','search_event',
    'search_job','candidate','lead','judgment','delivered_record','feedback',
    'provider_call','llm_call','error_event','audit_log'
  ] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %I USING (workspace_id = app_workspace_id())
         WITH CHECK (workspace_id = app_workspace_id())', t);
  END LOOP;
END $$;

-- Suppression: tenants READ their own rows + global rows, but WRITE only their own.
ALTER TABLE suppression ENABLE ROW LEVEL SECURITY;
CREATE POLICY suppression_read ON suppression FOR SELECT
  USING (workspace_id IS NULL OR workspace_id = app_workspace_id());
CREATE POLICY suppression_write ON suppression FOR INSERT
  WITH CHECK (workspace_id = app_workspace_id());
CREATE POLICY suppression_delete ON suppression FOR DELETE
  USING (workspace_id = app_workspace_id());

-- ---------------------------------------------------------------------------
-- 6. Grants for the runtime roles
-- ---------------------------------------------------------------------------
GRANT USAGE ON SCHEMA public TO sn_app, sn_system;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO sn_app, sn_system;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO sn_app, sn_system;
GRANT EXECUTE ON FUNCTION app_workspace_id() TO sn_app, sn_system;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO sn_app, sn_system;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO sn_app, sn_system;
