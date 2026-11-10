"""
Shared test setup.

* Points every database role at the `sales_navigator_test` database BEFORE any
  app module reads its settings (so tests can never touch dev/prod data).
* Forces offline AI + demo data, so tests are deterministic and free.
* Rebuilds the test schema once per test session (migrate + seed).

Run:  pytest                 (everything)
      pytest tests/unit      (no database needed)
      pytest -m golden       (end-to-end golden cases only)
"""
from __future__ import annotations

import os
import uuid

_TEST_DB = "sales_navigator_test"
os.environ.update({
    "SN_ENV": "test",
    "SN_DATABASE_OWNER_URL": f"postgresql+psycopg://sn_owner:sn_owner_dev_pw@localhost:5432/{_TEST_DB}",
    "SN_DATABASE_URL": f"postgresql+psycopg://sn_app:sn_app_dev_pw@localhost:5432/{_TEST_DB}",
    "SN_DATABASE_SYSTEM_URL": f"postgresql+psycopg://sn_system:sn_system_dev_pw@localhost:5432/{_TEST_DB}",
    "SN_DATA_MODE": "demo",
    "SN_LLM_MODE": "offline",
    "SN_VERIFIER_MODE": "fake",
    # Hermetic: tests never read real keys from backend/.env, so they can never call
    # a paid API, and encryption uses a fixed test key.
    "ANTHROPIC_API_KEY": "",
    "SN_BRAVE_API_KEY": "",
    "SN_ENCRYPTION_KEY": "",
})

import pytest  # noqa: E402

from app.core.context import RequestContext  # noqa: E402


@pytest.fixture(scope="session")
def db():
    """Fresh, migrated, seeded test database (once per session)."""
    from app.cli import reset_db

    reset_db()
    return True


@pytest.fixture
def workspace(db):
    """A brand-new signed-up customer: returns (RequestContext, session dict)."""
    from app.modules.identity.service import signup

    s = signup(email=f"user-{uuid.uuid4().hex[:8]}@test.example", password="correct-horse-1",
               full_name="Test User", company_name="Test Co")
    ctx = RequestContext(uuid.UUID(s["user"]["id"]), uuid.UUID(s["workspace_id"]), "owner")
    return ctx, s


@pytest.fixture
def client(db):
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)
