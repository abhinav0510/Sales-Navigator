"""
ICP service: build a draft from the seller's description (+ website), version it,
and let the user edit it.

Versioning: an ICP is never edited in place. Every edit inserts a new row with
the same `lineage_id` and `version + 1`; searches record which version they
used, so results are always reproducible.
"""
from __future__ import annotations

import uuid

from app.core.context import RequestContext
from app.core.db import as_json, execute, fetch_all, fetch_one, tenant_tx
from app.core.errors import NotFound
from app.core.ids import new_id
from app.modules.ai.guardrails import sanitize_evidence
from app.modules.ai.service import AIScope, build_icp
from app.modules.icp.schemas import IcpBody, IcpDraftRequest
from app.modules.knowledge.normalize import registrable_domain
from app.modules.providers.gateway import build_gateway
from app.modules.providers.website import is_safe_url


def _seller_website_text(url: str | None) -> str:
    """Read up to 3 pages of the seller's own site (SSRF-guarded in live mode)."""
    if not url:
        return ""
    if not url.startswith("http"):
        url = "https://" + url
    if not is_safe_url(url, resolve=False):
        return ""
    gw = build_gateway()
    texts = []
    for page in [url.rstrip("/") + "/", url.rstrip("/") + "/about", url.rstrip("/") + "/pricing"]:
        try:
            text = gw.fetch_page(page)
        except Exception:
            text = None
        if text:
            clean, _ = sanitize_evidence(text)
            texts.append(clean)
    return "\n".join(texts)[:6000]


def create_draft(ctx: RequestContext, req: IcpDraftRequest) -> dict:
    seeds = sorted({d for s in req.seeds if (d := registrable_domain(s) or s.strip().lower())})
    body = build_icp(AIScope(workspace_id=ctx.workspace_id), business_name=req.business_name,
                     description=req.description, website_text=_seller_website_text(req.website_url), seeds=seeds)
    lineage = new_id()
    return _insert(ctx, lineage, 1, body, {"business_name": req.business_name, "description": req.description,
                                          "website_url": req.website_url, "seeds": seeds})


def update(ctx: RequestContext, icp_id: uuid.UUID, body: IcpBody) -> dict:
    current = get(ctx, icp_id)
    with tenant_tx(ctx.workspace_id) as conn:
        execute(conn, "UPDATE icp SET status = 'archived' WHERE lineage_id = :l", {"l": current["lineage_id"]})
    return _insert(ctx, uuid.UUID(current["lineage_id"]), current["version"] + 1, body, current["source"])


def get(ctx: RequestContext, icp_id: uuid.UUID) -> dict:
    with tenant_tx(ctx.workspace_id) as conn:
        row = fetch_one(conn, "SELECT * FROM icp WHERE id = :id", {"id": icp_id})
    if not row:
        raise NotFound("ICP not found")
    return _out(row)


def list_active(ctx: RequestContext) -> list[dict]:
    with tenant_tx(ctx.workspace_id) as conn:
        rows = fetch_all(conn, "SELECT * FROM icp WHERE status = 'active' ORDER BY created_at DESC")
    return [_out(r) for r in rows]


def _insert(ctx: RequestContext, lineage: uuid.UUID, version: int, body: IcpBody, source: dict) -> dict:
    icp_id = new_id()
    with tenant_tx(ctx.workspace_id) as conn:
        execute(conn, """INSERT INTO icp (id, workspace_id, lineage_id, version, status, body, source, created_by)
                         VALUES (:id, :ws, :l, :v, 'active', CAST(:b AS jsonb), CAST(:s AS jsonb), :u)""",
                {"id": icp_id, "ws": ctx.workspace_id, "l": lineage, "v": version,
                 "b": as_json(body.model_dump()), "s": as_json(source), "u": ctx.user_id})
        row = fetch_one(conn, "SELECT * FROM icp WHERE id = :id", {"id": icp_id})
    return _out(row)


def _out(row: dict) -> dict:
    return {"id": str(row["id"]), "lineage_id": str(row["lineage_id"]), "version": row["version"],
            "status": row["status"], "body": row["body"], "source": row["source"],
            "created_at": row["created_at"].isoformat()}
