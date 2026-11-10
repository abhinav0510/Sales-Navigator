"""
Request context + authentication dependencies for FastAPI routes.

Every authenticated route receives a `RequestContext`:

    @router.get("/v1/searches")
    def list_searches(ctx: RequestContext = Depends(require_user)): ...

The context is the ONLY way a service learns who is calling and for which
workspace. Services pass `ctx.workspace_id` to `tenant_tx(...)`, which is what
switches on row-level security in Postgres.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Depends, Header

from app.core.errors import Forbidden, Unauthorized
from app.core.security import decode_access_token

# Roles from least to most privileged. `platform_admin` is the operator
# (the company running the SaaS), not a customer role.
ROLE_ORDER = ["viewer", "member", "admin", "owner", "platform_admin"]


@dataclass(frozen=True)
class RequestContext:
    user_id: uuid.UUID
    workspace_id: uuid.UUID
    role: str

    def has_role(self, minimum: str) -> bool:
        return ROLE_ORDER.index(self.role) >= ROLE_ORDER.index(minimum)


def require_user(authorization: str | None = Header(default=None)) -> RequestContext:
    """Decode `Authorization: Bearer <jwt>` into a RequestContext."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("Missing bearer token")
    claims = decode_access_token(authorization.split(" ", 1)[1])
    return RequestContext(
        user_id=uuid.UUID(claims["sub"]),
        workspace_id=uuid.UUID(claims["ws"]),
        role=claims["role"],
    )


def require_role(minimum: str):
    """Dependency factory: `Depends(require_role("admin"))`."""

    def _check(ctx: RequestContext = Depends(require_user)) -> RequestContext:
        if not ctx.has_role(minimum):
            raise Forbidden(f"Requires role '{minimum}' or higher")
        return ctx

    return _check
