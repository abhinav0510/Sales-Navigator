"""
HTTP routes (application tier entry points). Every route is thin:
validate input -> call ONE module service -> return its result.
No business logic lives here.

Auth: routes take `ctx = Depends(require_user)` (or require_role) - the
context carries the caller's workspace, which switches on row-level security.
"""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, EmailStr, Field

from app.core.context import RequestContext, require_role, require_user
from app.core.db import tenant_tx
from app.modules.admin import service as admin
from app.modules.billing import service as billing
from app.modules.compliance import service as compliance
from app.modules.geo.resolver import resolve
from app.modules.icp import service as icp
from app.modules.icp.schemas import IcpBody, IcpDraftRequest
from app.modules.identity import service as identity
from app.modules.leads import service as leads
from app.modules.search import service as searches

router = APIRouter(prefix="/v1")


# --------------------------------------------------------------------------- auth
class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    full_name: str = Field(min_length=1, max_length=120)
    company_name: str = Field(min_length=1, max_length=120)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


@router.post("/auth/signup", status_code=201, tags=["auth"])
def signup(body: SignupIn):
    return identity.signup(**body.model_dump())


@router.post("/auth/login", tags=["auth"])
def login(body: LoginIn):
    return identity.login(email=body.email, password=body.password)


@router.get("/me", tags=["auth"])
def me(ctx: RequestContext = Depends(require_user)):
    return identity.me(ctx.user_id, ctx.workspace_id, ctx.role)


# --------------------------------------------------------------------------- billing
class PlanIn(BaseModel):
    plan_code: str


@router.get("/billing/plans", tags=["billing"])
def plans(ctx: RequestContext = Depends(require_user)):
    with tenant_tx(ctx.workspace_id) as conn:
        return billing.list_plans(conn)


@router.get("/billing/usage", tags=["billing"])
def usage(ctx: RequestContext = Depends(require_user)):
    with tenant_tx(ctx.workspace_id) as conn:
        return billing.usage(conn, ctx.workspace_id)


@router.post("/billing/plan", tags=["billing"])
def change_plan(body: PlanIn, ctx: RequestContext = Depends(require_role("owner"))):
    with tenant_tx(ctx.workspace_id) as conn:
        return billing.change_plan(conn, ctx.workspace_id, body.plan_code)


# --------------------------------------------------------------------------- geo
class GeoIn(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    attempt: int = Field(1, ge=1, le=10)


@router.post("/geo/resolve", tags=["geo"])
def geo_resolve(body: GeoIn, ctx: RequestContext = Depends(require_user)):
    return resolve(body.text, attempt=body.attempt).to_dict()


# --------------------------------------------------------------------------- ICP
@router.post("/icp/draft", status_code=201, tags=["icp"])
def icp_draft(body: IcpDraftRequest, ctx: RequestContext = Depends(require_role("member"))):
    return icp.create_draft(ctx, body)


@router.get("/icp", tags=["icp"])
def icp_list(ctx: RequestContext = Depends(require_user)):
    return icp.list_active(ctx)


@router.get("/icp/{icp_id}", tags=["icp"])
def icp_get(icp_id: uuid.UUID, ctx: RequestContext = Depends(require_user)):
    return icp.get(ctx, icp_id)


@router.put("/icp/{icp_id}", tags=["icp"])
def icp_update(icp_id: uuid.UUID, body: IcpBody, ctx: RequestContext = Depends(require_role("member"))):
    return icp.update(ctx, icp_id, body)


# --------------------------------------------------------------------------- searches
class SearchIn(BaseModel):
    icp_id: uuid.UUID
    region_text: str = Field(min_length=1, max_length=300)
    target_count: int = Field(20, ge=1, le=1000)
    scopes: list[dict] | None = None              # pre-resolved scopes from /geo/resolve
    budget_paise: int | None = Field(None, ge=0)
    include_previously_delivered: bool = False


class ClarifyIn(BaseModel):
    option_index: int = Field(ge=0)


class PathIn(BaseModel):
    path: Literal["B2B_COMPANY", "LOCAL_BUSINESS", "LOOKALIKE", "SIGNAL_LED", "B2B2C"]


class BudgetIn(BaseModel):
    budget_paise: int = Field(gt=0)


@router.post("/searches", status_code=201, tags=["searches"])
def search_create(body: SearchIn, ctx: RequestContext = Depends(require_role("member"))):
    return searches.create(ctx, **body.model_dump())


@router.get("/searches", tags=["searches"])
def search_list(ctx: RequestContext = Depends(require_user)):
    return searches.list_searches(ctx)


@router.get("/searches/{search_id}", tags=["searches"])
def search_get(search_id: uuid.UUID, ctx: RequestContext = Depends(require_user)):
    return searches.get(ctx, search_id)


@router.post("/searches/{search_id}/clarify", tags=["searches"])
def search_clarify(search_id: uuid.UUID, body: ClarifyIn, ctx: RequestContext = Depends(require_role("member"))):
    return searches.answer_question(ctx, search_id, body.option_index)


@router.put("/searches/{search_id}/plan", tags=["searches"])
def search_override(search_id: uuid.UUID, body: PathIn, ctx: RequestContext = Depends(require_role("member"))):
    return searches.override_path(ctx, search_id, body.path)


@router.post("/searches/{search_id}/run", tags=["searches"])
def search_run(search_id: uuid.UUID, ctx: RequestContext = Depends(require_role("member"))):
    return searches.run(ctx, search_id)


@router.post("/searches/{search_id}/budget", tags=["searches"])
def search_budget(search_id: uuid.UUID, body: BudgetIn, ctx: RequestContext = Depends(require_role("member"))):
    return searches.raise_budget(ctx, search_id, body.budget_paise)


@router.post("/searches/{search_id}/cancel", tags=["searches"])
def search_cancel(search_id: uuid.UUID, ctx: RequestContext = Depends(require_role("member"))):
    return searches.cancel(ctx, search_id)


@router.get("/searches/{search_id}/timeline", tags=["searches"])
def search_timeline(search_id: uuid.UUID, ctx: RequestContext = Depends(require_user)):
    return admin.search_timeline(ctx, search_id, platform=False)


# --------------------------------------------------------------------------- leads
class FeedbackIn(BaseModel):
    verdict: Literal["up", "down"]
    reason: str | None = Field(None, max_length=300)


@router.get("/searches/{search_id}/leads", tags=["leads"])
def lead_list(search_id: uuid.UUID, decision: str | None = Query(None), ctx: RequestContext = Depends(require_user)):
    return leads.list_for_search(ctx, search_id, decision)


@router.get("/searches/{search_id}/export.csv", response_class=PlainTextResponse, tags=["leads"])
def lead_export(search_id: uuid.UUID, ctx: RequestContext = Depends(require_role("member"))):
    return PlainTextResponse(leads.export_csv(ctx, search_id), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="leads-{search_id}.csv"'})


@router.get("/leads/{lead_id}", tags=["leads"])
def lead_get(lead_id: uuid.UUID, ctx: RequestContext = Depends(require_user)):
    return leads.get_lead(ctx, lead_id)


@router.post("/leads/{lead_id}/feedback", status_code=204, tags=["leads"])
def lead_feedback(lead_id: uuid.UUID, body: FeedbackIn, ctx: RequestContext = Depends(require_user)):
    leads.add_feedback(ctx, lead_id, body.verdict, body.reason)


@router.post("/leads/{lead_id}/accept", tags=["leads"])
def lead_accept(lead_id: uuid.UUID, ctx: RequestContext = Depends(require_role("member"))):
    return leads.accept_hold(ctx, lead_id)


# --------------------------------------------------------------------------- compliance
class SuppressIn(BaseModel):
    kind: Literal["email", "phone", "domain"]
    values: list[str] = Field(min_length=1, max_length=10000)


class ErasureIn(BaseModel):
    email: EmailStr


@router.get("/compliance/suppressions", tags=["compliance"])
def suppress_list(ctx: RequestContext = Depends(require_role("admin"))):
    return compliance.list_suppressions(ctx.workspace_id)


@router.post("/compliance/suppressions", tags=["compliance"])
def suppress_add(body: SuppressIn, ctx: RequestContext = Depends(require_role("admin"))):
    return {"added": compliance.add_suppressions(ctx.workspace_id, body.kind, body.values)}


@router.post("/compliance/erasure", tags=["compliance"])
def erasure(body: ErasureIn, ctx: RequestContext = Depends(require_role("platform_admin"))):
    return compliance.erase_person_everywhere(body.email)


# --------------------------------------------------------------------------- admin / observability
@router.get("/admin/overview", tags=["admin"])
def admin_overview(platform: bool = False, days: int = 30, ctx: RequestContext = Depends(require_role("admin"))):
    return admin.overview(ctx, platform, days)


@router.get("/admin/ai", tags=["admin"])
def admin_ai(platform: bool = False, days: int = 30, ctx: RequestContext = Depends(require_role("admin"))):
    return admin.ai_calls(ctx, platform, days)


@router.get("/admin/cost", tags=["admin"])
def admin_cost(platform: bool = False, days: int = 14, ctx: RequestContext = Depends(require_role("admin"))):
    return admin.cost(ctx, platform, days)


@router.get("/admin/providers", tags=["admin"])
def admin_providers(platform: bool = False, ctx: RequestContext = Depends(require_role("admin"))):
    return admin.provider_health(ctx, platform)


@router.get("/admin/errors", tags=["admin"])
def admin_errors(platform: bool = False, ctx: RequestContext = Depends(require_role("admin"))):
    return admin.errors(ctx, platform)


@router.get("/admin/searches/{search_id}/timeline", tags=["admin"])
def admin_timeline(search_id: uuid.UUID, platform: bool = False, ctx: RequestContext = Depends(require_role("admin"))):
    return admin.search_timeline(ctx, search_id, platform)
