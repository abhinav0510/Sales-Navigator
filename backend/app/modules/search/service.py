"""
Search service - what the API calls. The heavy work runs in the worker
(orchestrator.py); this module only creates, plans, starts and reports.

State machine (LLD 5.1):

    PLANNING -> NEEDS_INPUT (planner unsure) -> PLANNING (user answered)
    PLANNING -> ESTIMATED   (plan + cost shown to the user)
    ESTIMATED -> RUNNING    (quota reserved, job queued)
    RUNNING  -> PAUSED_BUDGET -> RUNNING (budget raised)
    RUNNING  -> COMPLETED | FAILED
    any non-final -> CANCELLED (unused quota released)
"""
from __future__ import annotations

import uuid

from app.core.config import load_yaml
from app.core.context import RequestContext
from app.core.db import as_json, execute, fetch_all, fetch_one, tenant_tx
from app.core.errors import Conflict, NotFound, ProviderUnavailable, ValidationFailed
from app.core.ids import new_id
from app.modules.billing import service as billing
from app.modules.geo.resolver import resolve
from app.modules.icp.schemas import IcpBody
from app.modules.observability.recorder import record_search_event
from app.modules.planner.service import plan_search
from app.modules.providers.gateway import build_gateway
from app.modules.search import jobs

FINAL = ("COMPLETED", "FAILED", "CANCELLED")


def _load(conn, search_id: uuid.UUID) -> dict:
    row = fetch_one(conn, "SELECT * FROM search WHERE id = :id", {"id": search_id})
    if not row:
        raise NotFound("Search not found")
    return row


def _plan_and_store(conn, search: dict, icp_body: IcpBody, seeds: list[str], forced_path: str | None) -> dict:
    plan = plan_search(icp_body, seeds=seeds, available_sources=build_gateway().available_sources,
                       target_count=search["target_count"], forced_path=forced_path)
    status = "NEEDS_INPUT" if plan.question else "ESTIMATED"
    cfg = load_yaml("pipeline")
    budget = search["budget_paise"] or max(int(plan.estimate["cost_paise"] * cfg["budget_factor"]),
                                           cfg["min_budget_paise"])
    execute(conn, """UPDATE search SET status = :st, plan = CAST(:p AS jsonb), estimate = CAST(:e AS jsonb),
                       question = CAST(:q AS jsonb), budget_paise = :b, updated_at = now() WHERE id = :id""",
            {"st": status, "p": as_json(plan.to_dict()), "e": as_json(plan.estimate),
             "q": as_json(plan.question) if plan.question else None, "b": budget, "id": search["id"]})
    return _load(conn, search["id"])


def create(ctx: RequestContext, *, icp_id: uuid.UUID, region_text: str, target_count: int,
           scopes: list[dict] | None = None, budget_paise: int | None = None,
           include_previously_delivered: bool = False) -> dict:
    if not 1 <= target_count <= 1000:
        raise ValidationFailed("target_count must be between 1 and 1000")
    if scopes is None:
        res = resolve(region_text)
        if res.status != "resolved":
            # The UI shows suggestions / options; the user picks, then calls again with `scopes`.
            raise ValidationFailed("Region needs clarification", code="region_" + res.status, extra=res.to_dict())
        scopes = [s.__dict__ for s in res.scopes]

    with tenant_tx(ctx.workspace_id) as conn:
        icp = fetch_one(conn, "SELECT * FROM icp WHERE id = :id", {"id": icp_id})
        if not icp:
            raise NotFound("ICP not found")
        _refuse_hopeless(IcpBody(**icp["body"]))
        sid = new_id()
        execute(conn, """INSERT INTO search (id, workspace_id, icp_id, region_text, geoscope, status, target_count,
                                             budget_paise, include_previously_delivered, created_by)
                         VALUES (:id, :ws, :icp, :rt, CAST(:g AS jsonb), 'PLANNING', :t, :b, :inc, :u)""",
                {"id": sid, "ws": ctx.workspace_id, "icp": icp_id, "rt": region_text,
                 "g": as_json({"scopes": scopes}), "t": target_count, "b": budget_paise or 0,
                 "inc": include_previously_delivered, "u": ctx.user_id})
        search = _plan_and_store(conn, _load(conn, sid), IcpBody(**icp["body"]),
                                 icp["source"].get("seeds", []), None)
        paths = [p["path"] for p in search["plan"]["paths"]]
        if paths == ["AUDIENCE_INSIGHT"] and IcpBody(**icp["body"]).wants_contacts:
            # Raising inside the transaction rolls the search back: nothing saved, nothing spent.
            raise ProviderUnavailable(
                "No data source can find these leads: web search is not configured "
                "(set SN_BRAVE_API_KEY, or use demo mode).", code="no_data_source",
                extra={"rationale": search["plan"]["rationale"]})
    record_search_event(workspace_id=ctx.workspace_id, search_id=sid, kind="state",
                        message=f"Search planned -> {search['status']}", data={"plan": search["plan"]})
    return out(search)


def _refuse_hopeless(icp: IcpBody) -> None:
    """A profile with nothing to match against can only waste money: stop before planning."""
    if not icp.industry_keywords and not icp.place_categories:
        raise ValidationFailed(
            "Your customer profile has no words that identify a matching company, so no search can find "
            "anyone. Open Customer profile and add industries / fit words (or rebuild it).",
            code="profile_too_vague")


def answer_question(ctx: RequestContext, search_id: uuid.UUID, option_index: int) -> dict:
    with tenant_tx(ctx.workspace_id) as conn:
        search = _load(conn, search_id)
        if search["status"] not in ("NEEDS_INPUT", "ESTIMATED"):
            raise Conflict("Search is not waiting for an answer")
        options = (search["question"] or {}).get("options", [])
        if not 0 <= option_index < len(options):
            raise ValidationFailed("Unknown option")
        return out(_replan(conn, search, options[option_index]["path"]))


def override_path(ctx: RequestContext, search_id: uuid.UUID, path: str) -> dict:
    with tenant_tx(ctx.workspace_id) as conn:
        search = _load(conn, search_id)
        if search["status"] not in ("NEEDS_INPUT", "ESTIMATED"):
            raise Conflict("Plan can only be changed before the search runs")
        return out(_replan(conn, search, path))


def _replan(conn, search: dict, path: str) -> dict:
    icp = fetch_one(conn, "SELECT * FROM icp WHERE id = :id", {"id": search["icp_id"]})
    return _plan_and_store(conn, search, IcpBody(**icp["body"]), icp["source"].get("seeds", []), path)


def run(ctx: RequestContext, search_id: uuid.UUID) -> dict:
    with tenant_tx(ctx.workspace_id) as conn:
        search = _load(conn, search_id)
        if search["status"] != "ESTIMATED":
            raise Conflict(f"Search cannot start from status {search['status']}")
        billing.reserve(conn, ctx.workspace_id, search_id, search["target_count"])
        execute(conn, """UPDATE search SET status = 'RUNNING', stage = 'queued', quota_reserved = :n,
                           updated_at = now() WHERE id = :id""", {"n": search["target_count"], "id": search_id})
        jobs.enqueue(conn, ctx.workspace_id, search_id)
        search = _load(conn, search_id)
    record_search_event(workspace_id=ctx.workspace_id, search_id=search_id, kind="billing",
                        message=f"Reserved {search['target_count']} leads from quota; job queued")
    return out(search)


def raise_budget(ctx: RequestContext, search_id: uuid.UUID, budget_paise: int) -> dict:
    with tenant_tx(ctx.workspace_id) as conn:
        search = _load(conn, search_id)
        if budget_paise <= search["budget_paise"]:
            raise ValidationFailed("New budget must be higher than the current one")
        execute(conn, "UPDATE search SET budget_paise = :b, updated_at = now() WHERE id = :id",
                {"b": budget_paise, "id": search_id})
        if search["status"] == "PAUSED_BUDGET":
            execute(conn, "UPDATE search SET status = 'RUNNING' WHERE id = :id", {"id": search_id})
            jobs.enqueue(conn, ctx.workspace_id, search_id)
        return out(_load(conn, search_id))


def cancel(ctx: RequestContext, search_id: uuid.UUID) -> dict:
    with tenant_tx(ctx.workspace_id) as conn:
        search = _load(conn, search_id)
        if search["status"] in FINAL:
            raise Conflict("Search already finished")
        unused = max(0, search["quota_reserved"] - search["delivered_count"])
        billing.release(conn, ctx.workspace_id, search_id, unused)
        execute(conn, """UPDATE search SET status = 'CANCELLED', quota_reserved = quota_reserved - :u,
                           completed_at = now(), updated_at = now() WHERE id = :id""", {"u": unused, "id": search_id})
        execute(conn, "UPDATE search_job SET status = 'done' WHERE search_id = :id AND status = 'queued'",
                {"id": search_id})
        return out(_load(conn, search_id))


def get(ctx: RequestContext, search_id: uuid.UUID) -> dict:
    with tenant_tx(ctx.workspace_id) as conn:
        return out(_load(conn, search_id))


def list_searches(ctx: RequestContext, limit: int = 50) -> list[dict]:
    with tenant_tx(ctx.workspace_id) as conn:
        rows = fetch_all(conn, "SELECT * FROM search ORDER BY created_at DESC LIMIT :l", {"l": limit})
    return [out(r) for r in rows]


def out(row: dict) -> dict:
    """API shape of a search."""
    keep = ("status", "stage", "region_text", "target_count", "delivered_count", "held_count", "rejected_count",
            "budget_paise", "spent_paise", "plan", "estimate", "question", "quota_reserved", "quality_report",
            "error", "include_previously_delivered")
    data = {k: row[k] for k in keep}
    data.update(id=str(row["id"]), icp_id=str(row["icp_id"]), scopes=row["geoscope"].get("scopes", []),
                created_at=row["created_at"].isoformat(),
                completed_at=row["completed_at"].isoformat() if row["completed_at"] else None)
    return data
