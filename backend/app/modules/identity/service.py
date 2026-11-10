"""
Identity: sign-up, login and "who am I".

Sign-up creates, in one go: the user, their workspace (the tenant), an owner
membership, and a trial subscription with its first month of lead quota.

Why login uses the system role for one query: a user may belong to several
workspaces, and memberships are tenant rows protected by row-level security.
Before login we don't yet know which workspace to open, so the single
"which workspaces is this user in?" lookup runs as the system role. Every
other request runs under the user's workspace with RLS on.
"""
from __future__ import annotations

import uuid

from app.core.config import load_yaml
from app.core.db import execute, fetch_all, fetch_one, public_tx, system_tx, tenant_tx
from app.core.errors import Conflict, Unauthorized
from app.core.ids import new_id
from app.core.security import create_access_token, hash_password, verify_password
from app.modules.billing.service import current_period, ensure_period_grant


def signup(*, email: str, password: str, full_name: str, company_name: str) -> dict:
    email = email.strip().lower()
    with public_tx() as conn:
        if fetch_one(conn, "SELECT 1 AS x FROM app_user WHERE lower(email) = :e", {"e": email}):
            raise Conflict("An account with this email already exists", code="email_taken")
        user_id = new_id()
        execute(conn, """INSERT INTO app_user (id, email, full_name, password_hash)
                         VALUES (:id, :e, :n, :h)""",
                {"id": user_id, "e": email, "n": full_name.strip(), "h": hash_password(password)})

    ws_id = new_id()
    with tenant_tx(ws_id) as conn:          # RLS: we may only write rows for this new workspace
        execute(conn, "INSERT INTO workspace (id, name) VALUES (:id, :n)", {"id": ws_id, "n": company_name.strip()})
        execute(conn, "INSERT INTO membership (workspace_id, user_id, role) VALUES (:ws, :u, 'owner')",
                {"ws": ws_id, "u": user_id})
        execute(conn, """INSERT INTO subscription (id, workspace_id, plan_code, status, current_period)
                         VALUES (:id, :ws, :plan, 'trialing', :p)""",
                {"id": new_id(), "ws": ws_id, "plan": load_yaml("plans")["new_workspace_plan"], "p": current_period()})
        ensure_period_grant(conn, ws_id)
    return _session(user_id, ws_id, "owner", email, full_name, company_name, is_platform_admin=False)


def login(*, email: str, password: str) -> dict:
    with public_tx() as conn:
        user = fetch_one(conn, "SELECT * FROM app_user WHERE lower(email) = lower(:e)", {"e": email.strip()})
    # Same error for unknown email and wrong password: never reveal which one failed.
    if not user or not verify_password(password, user["password_hash"]):
        raise Unauthorized("Email or password is incorrect", code="bad_credentials")
    with system_tx() as conn:
        memberships = fetch_all(conn, """SELECT m.workspace_id, m.role, w.name FROM membership m
                                         JOIN workspace w ON w.id = m.workspace_id
                                         WHERE m.user_id = :u ORDER BY w.created_at""", {"u": user["id"]})
    if not memberships:
        raise Unauthorized("This user has no workspace", code="no_workspace")
    m = memberships[0]
    return _session(user["id"], m["workspace_id"], m["role"], user["email"], user["full_name"], m["name"],
                    is_platform_admin=user["is_platform_admin"])


def me(user_id: uuid.UUID, workspace_id: uuid.UUID, role: str) -> dict:
    with public_tx() as conn:
        user = fetch_one(conn, "SELECT id, email, full_name, is_platform_admin FROM app_user WHERE id = :u",
                         {"u": user_id})
    with tenant_tx(workspace_id) as conn:
        ws = fetch_one(conn, "SELECT id, name FROM workspace WHERE id = :w", {"w": workspace_id})
    return {"user": user, "workspace": ws, "role": role}


def _session(user_id, ws_id, role, email, full_name, ws_name, *, is_platform_admin: bool) -> dict:
    token_role = "platform_admin" if is_platform_admin else role
    return {
        "access_token": create_access_token(user_id, ws_id, token_role),
        "token_type": "bearer",
        "user": {"id": str(user_id), "email": email, "full_name": full_name},
        "workspace_id": str(ws_id),
        "workspace_name": ws_name,
        "role": token_role,
    }
