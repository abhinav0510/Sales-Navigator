"""
Data-tier access (PostgreSQL).

Design choices a new developer should know:

* **SQL-first.** We use SQLAlchemy *Core* with plain, named-parameter SQL
  (`text(...)`), not an ORM. Every query is visible and greppable, and the
  row-level-security rules in the database stay obvious.

* **Three database roles** (created by `migrations/000_roles.sql`):
    - `sn_owner`  - owns the schema, runs migrations. Never used at runtime.
    - `sn_app`    - used for tenant requests. Subject to row-level security,
                    so it can only ever see rows of the workspace set below.
    - `sn_system` - used by the background worker and platform-operator views.
                    Has BYPASSRLS. Only code that genuinely spans tenants uses it.

* **Tenant isolation is enforced by Postgres, not just by our WHERE clauses.**
  `tenant_tx(workspace_id)` sets `app.workspace_id` for the transaction; every
  tenant table has a policy `workspace_id = current_setting('app.workspace_id')`.
  A forgotten WHERE clause therefore returns nothing instead of leaking data.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any, Literal

from sqlalchemy import Connection, Engine, create_engine, text

from app.core.config import get_settings

EngineKind = Literal["app", "system", "owner"]


@lru_cache
def get_engine(kind: EngineKind = "app") -> Engine:
    """One pooled engine per role, created lazily and reused for the process."""
    settings = get_settings()
    url = {
        "app": settings.database_url,
        "system": settings.database_system_url,
        "owner": settings.database_owner_url,
    }[kind]
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=10)


@contextmanager
def tenant_tx(workspace_id: uuid.UUID) -> Iterator[Connection]:
    """
    Open a transaction scoped to ONE workspace (row-level security active).

    Usage:
        with tenant_tx(ctx.workspace_id) as conn:
            rows = fetch_all(conn, "SELECT * FROM search")   # only this workspace
    """
    with get_engine("app").begin() as conn:
        # `true` = local to this transaction; the setting vanishes at COMMIT,
        # so a pooled connection can never carry one tenant's id to the next.
        conn.execute(
            text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": str(workspace_id)}
        )
        yield conn


@contextmanager
def system_tx() -> Iterator[Connection]:
    """Cross-tenant transaction (worker, platform operator). Bypasses RLS - use sparingly."""
    with get_engine("system").begin() as conn:
        yield conn


@contextmanager
def public_tx() -> Iterator[Connection]:
    """
    Transaction for requests that have no tenant yet (signup, login, geo lookup).
    Uses the app role; tenant tables return no rows because no workspace is set.
    """
    with get_engine("app").begin() as conn:
        yield conn


# ---------------------------------------------------------------------------
# Small query helpers - keep call sites short and readable.
# ---------------------------------------------------------------------------
def fetch_one(conn: Connection, sql: str, params: dict[str, Any] | None = None) -> dict | None:
    row = conn.execute(text(sql), params or {}).mappings().first()
    return dict(row) if row else None


def fetch_all(conn: Connection, sql: str, params: dict[str, Any] | None = None) -> list[dict]:
    return [dict(r) for r in conn.execute(text(sql), params or {}).mappings().all()]


def execute(conn: Connection, sql: str, params: dict[str, Any] | None = None) -> int:
    """Run a write. Returns the number of affected rows."""
    return conn.execute(text(sql), params or {}).rowcount


def as_json(value: Any) -> str:
    """
    Serialize to JSON for a `CAST(:param AS jsonb)` column.
    `sort_keys=True` keeps output deterministic (stable diffs, stable caching).
    """
    return json.dumps(value, default=str, sort_keys=True, ensure_ascii=False)
