"""Row-level security, billing quota ledger, suppression scoping. Needs PostgreSQL."""
import uuid

import pytest
from sqlalchemy.exc import DBAPIError

from app.core.db import execute, fetch_all, fetch_one, public_tx, system_tx, tenant_tx
from app.core.errors import QuotaExceeded
from app.core.ids import new_id
from app.modules.billing import service as billing
from app.modules.compliance.service import add_suppressions, is_suppressed

pytestmark = pytest.mark.integration


def test_rls_isolates_workspaces(workspace):
    ctx_a, _ = workspace
    from app.modules.identity.service import signup

    s_b = signup(email=f"b-{uuid.uuid4().hex[:6]}@test.example", password="pw-12345678", full_name="B",
                 company_name="B Co")
    ws_b = uuid.UUID(s_b["workspace_id"])
    with tenant_tx(ctx_a.workspace_id) as conn:
        seen = fetch_all(conn, "SELECT id FROM workspace")
        subs = fetch_all(conn, "SELECT workspace_id FROM subscription")
    assert [r["id"] for r in seen] == [ctx_a.workspace_id]
    assert {r["workspace_id"] for r in subs} == {ctx_a.workspace_id}
    with tenant_tx(ws_b) as conn:
        assert fetch_one(conn, "SELECT 1 AS x FROM workspace WHERE id = :id", {"id": ctx_a.workspace_id}) is None


def test_no_workspace_set_means_no_rows(workspace):
    with public_tx() as conn:
        assert fetch_all(conn, "SELECT * FROM subscription") == []
        assert fetch_all(conn, "SELECT * FROM quota_ledger") == []


def test_cannot_write_into_another_workspace(workspace):
    ctx, _ = workspace
    other = new_id()
    with pytest.raises(DBAPIError):
        with tenant_tx(ctx.workspace_id) as conn:
            execute(conn, """INSERT INTO quota_ledger (id, workspace_id, period, kind, delta, reason)
                             VALUES (:id, :ws, '2026-11', 'leads', 999, 'hack')""", {"id": new_id(), "ws": other})


def test_quota_grant_reserve_release(workspace):
    ctx, _ = workspace
    sid = new_id()
    with tenant_tx(ctx.workspace_id) as conn:
        assert billing.balance(conn, ctx.workspace_id) == 25            # trial plan
        billing.reserve(conn, ctx.workspace_id, sid, 20)
        assert billing.balance(conn, ctx.workspace_id) == 5
        with pytest.raises(QuotaExceeded):
            billing.reserve(conn, ctx.workspace_id, new_id(), 6)
        billing.release(conn, ctx.workspace_id, sid, 12)
        assert billing.balance(conn, ctx.workspace_id) == 17
        usage = billing.usage(conn, ctx.workspace_id)
    assert usage["plan"] == "trial" and usage["remaining"] == 17


def test_upgrade_tops_up_quota(workspace):
    ctx, _ = workspace
    with tenant_tx(ctx.workspace_id) as conn:
        usage = billing.change_plan(conn, ctx.workspace_id, "growth")
    assert usage["plan"] == "growth" and usage["remaining"] == 600


def test_suppression_tenant_and_global(workspace):
    ctx, _ = workspace
    from app.modules.identity.service import signup

    other = uuid.UUID(signup(email=f"o-{uuid.uuid4().hex[:6]}@test.example", password="pw-12345678",
                             full_name="O", company_name="O")["workspace_id"])
    add_suppressions(ctx.workspace_id, "email", ["Blocked@Brand.example"])
    with tenant_tx(ctx.workspace_id) as conn:
        assert is_suppressed(conn, "email", "blocked@brand.example")
    with tenant_tx(other) as conn:
        assert not is_suppressed(conn, "email", "blocked@brand.example")     # tenant list is private
    # Global (erasure) suppression is visible to every workspace...
    from app.modules.compliance.service import erase_person_everywhere

    erase_person_everywhere("erased@brand.example")
    with tenant_tx(other) as conn:
        assert is_suppressed(conn, "email", "erased@brand.example")
    # ...but a tenant cannot create a global row.
    with pytest.raises(DBAPIError):
        with tenant_tx(ctx.workspace_id) as conn:
            execute(conn, """INSERT INTO suppression (id, workspace_id, kind, value_hmac)
                             VALUES (:id, NULL, 'email', '\\x00')""", {"id": new_id()})
