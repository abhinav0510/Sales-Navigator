#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# One-time PostgreSQL setup for Sales Navigator (local or a fresh server).
#
# Creates three roles and two databases:
#   sn_owner   owns the schema, runs migrations          (LOGIN)
#   sn_app     API runtime, subject to row-level security (LOGIN, NOBYPASSRLS)
#   sn_system  worker + operator views, bypasses RLS      (LOGIN, BYPASSRLS)
#
#   sales_navigator        main database
#   sales_navigator_test   used by the integration / golden tests
#
# Run as a Postgres superuser (on Homebrew Postgres that is your macOS user):
#   ./scripts/setup_db.sh
# Override passwords for production:
#   SN_OWNER_PW=... SN_APP_PW=... SN_SYSTEM_PW=... ./scripts/setup_db.sh
# Safe to re-run: existing roles/databases are left in place.
# ---------------------------------------------------------------------------
set -euo pipefail

OWNER_PW="${SN_OWNER_PW:-sn_owner_dev_pw}"
APP_PW="${SN_APP_PW:-sn_app_dev_pw}"
SYSTEM_PW="${SN_SYSTEM_PW:-sn_system_dev_pw}"
PSQL="psql -v ON_ERROR_STOP=1 -d postgres -q"

echo "==> Creating roles (if missing)"
$PSQL <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sn_owner') THEN
    CREATE ROLE sn_owner LOGIN PASSWORD '${OWNER_PW}';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sn_app') THEN
    CREATE ROLE sn_app LOGIN PASSWORD '${APP_PW}' NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sn_system') THEN
    CREATE ROLE sn_system LOGIN PASSWORD '${SYSTEM_PW}' BYPASSRLS;
  END IF;
END
\$\$;
SQL

for DB in sales_navigator sales_navigator_test; do
  if ! psql -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='${DB}'" | grep -q 1; then
    echo "==> Creating database ${DB} (owner sn_owner)"
    psql -d postgres -q -c "CREATE DATABASE ${DB} OWNER sn_owner"
  else
    echo "==> Database ${DB} already exists"
  fi
done

echo "==> Done. Next: python -m app.cli migrate && python -m app.cli seed"
