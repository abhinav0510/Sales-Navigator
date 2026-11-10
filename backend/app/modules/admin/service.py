"""
Operator observability views (OBSERVABILITY_AND_QA.md part F).

Two scopes:
  workspace  - a customer's admin sees their own workspace (row-level security)
  platform   - the platform operator sees every workspace (system role)

Every view reads ledgers the pipeline already writes: provider_call, llm_call,
error_event, search_event, lead, search. Nothing here changes data.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager

from app.core.config import get_settings
from app.core.context import RequestContext
from app.core.db import fetch_all, fetch_one, system_tx, tenant_tx


@contextmanager
def _tx(ctx: RequestContext, platform: bool):
    if platform and ctx.role == "platform_admin":
        with system_tx() as conn:
            yield conn
    else:
        with tenant_tx(ctx.workspace_id) as conn:
            yield conn


def _inr(micro_usd) -> float:
    # Postgres SUM() returns NUMERIC (Python Decimal): convert before mixing with float.
    return round(float(micro_usd or 0) / 1_000_000 * get_settings().fx_usd_inr, 2)


def overview(ctx: RequestContext, platform: bool, days: int = 30) -> dict:
    with _tx(ctx, platform) as conn:
        spend = fetch_one(conn, f"""SELECT COALESCE(sum(cost_micro_usd),0) AS c, count(*) AS n,
                                    count(*) FILTER (WHERE cache_hit) AS hits
                                    FROM provider_call WHERE at > now() - interval '{days} days'""")
        ai = fetch_one(conn, f"""SELECT COALESCE(sum(cost_micro_usd),0) AS c, count(*) AS n,
                                 count(*) FILTER (WHERE status <> 'ok') AS failed
                                 FROM llm_call WHERE at > now() - interval '{days} days'""")
        leads = fetch_one(conn, f"""SELECT count(*) FILTER (WHERE decision IN ('pass','degrade')) AS delivered,
                                    count(*) FILTER (WHERE decision = 'hold') AS held,
                                    count(*) FILTER (WHERE decision = 'reject') AS rejected
                                    FROM lead WHERE created_at > now() - interval '{days} days'""")
        searches = fetch_one(conn, f"""SELECT count(*) AS n, count(*) FILTER (WHERE status = 'COMPLETED') AS done,
                                       count(*) FILTER (WHERE status = 'FAILED') AS failed
                                       FROM search WHERE created_at > now() - interval '{days} days'""")
        errors = fetch_one(conn, f"SELECT count(*) AS n FROM error_event WHERE at > now() - interval '{days} days'")
    total_inr = _inr(spend["c"]) + _inr(ai["c"])
    return {
        "days": days,
        "spend_inr": {"providers": _inr(spend["c"]), "ai": _inr(ai["c"]), "total": round(total_inr, 2)},
        "provider_calls": spend["n"], "cache_hit_rate": round(spend["hits"] / spend["n"], 3) if spend["n"] else None,
        "ai_calls": ai["n"], "ai_failures": ai["failed"],
        "leads": leads, "searches": searches, "errors": errors["n"],
        "cost_per_delivered_lead_inr": round(total_inr / leads["delivered"], 2) if leads["delivered"] else None,
    }


def ai_calls(ctx: RequestContext, platform: bool, days: int = 30) -> dict:
    with _tx(ctx, platform) as conn:
        by_task = fetch_all(conn, f"""
            SELECT task, model, count(*) AS calls,
                   count(*) FILTER (WHERE status = 'ok') AS ok,
                   sum(tokens_in) AS tokens_in, sum(tokens_cached) AS tokens_cached, sum(tokens_out) AS tokens_out,
                   sum(cost_micro_usd) AS cost, sum(evidence_drops) AS evidence_drops,
                   count(*) FILTER (WHERE fallback_used) AS fallbacks,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95
            FROM llm_call WHERE at > now() - interval '{days} days' GROUP BY task, model ORDER BY task, model""")
        recent = fetch_all(conn, """SELECT task, model, status, tokens_in, tokens_cached, tokens_out, cost_micro_usd,
                                    latency_ms, fallback_used, evidence_drops, at, search_id::text AS search_id
                                    FROM llm_call ORDER BY at DESC LIMIT 50""")
    for r in by_task:
        total_in = (r["tokens_in"] or 0) + (r["tokens_cached"] or 0)
        r["cached_ratio"] = round((r["tokens_cached"] or 0) / total_in, 3) if total_in else None
        r["cost_inr"] = _inr(r.pop("cost"))
        r["success_rate"] = round(r["ok"] / r["calls"], 3) if r["calls"] else None
    for r in recent:
        r["cost_inr"] = _inr(r.pop("cost_micro_usd"))
    return {"by_task": by_task, "recent": recent}


def cost(ctx: RequestContext, platform: bool, days: int = 30) -> dict:
    with _tx(ctx, platform) as conn:
        daily = fetch_all(conn, f"""
            SELECT d::date AS day,
                   COALESCE((SELECT sum(cost_micro_usd) FROM provider_call p WHERE p.at::date = d::date), 0) AS providers,
                   COALESCE((SELECT sum(cost_micro_usd) FROM llm_call a WHERE a.at::date = d::date), 0) AS ai
            FROM generate_series(now() - interval '{days - 1} days', now(), interval '1 day') d ORDER BY 1""")
        by_provider = fetch_all(conn, f"""SELECT provider, op, count(*) AS calls, sum(cost_micro_usd) AS cost,
                                          count(*) FILTER (WHERE cache_hit) AS cache_hits
                                          FROM provider_call WHERE at > now() - interval '{days} days'
                                          GROUP BY provider, op ORDER BY cost DESC NULLS LAST""")
        by_search = fetch_all(conn, """SELECT id::text AS id, region_text, status, delivered_count, spent_paise, created_at
                                       FROM search ORDER BY spent_paise DESC LIMIT 10""")
    for d in daily:
        d["providers_inr"], d["ai_inr"] = _inr(d.pop("providers")), _inr(d.pop("ai"))
    for p in by_provider:
        p["cost_inr"] = _inr(p.pop("cost"))
    cap = get_settings().platform_daily_spend_cap_inr
    today = daily[-1]["providers_inr"] + daily[-1]["ai_inr"] if daily else 0
    return {"daily": daily, "by_provider": by_provider, "top_searches": by_search,
            "platform_daily_cap_inr": cap, "today_inr": round(today, 2),
            "cap_used_pct": round(100 * today / cap, 1) if cap else None}


def provider_health(ctx: RequestContext, platform: bool, days: int = 7) -> list[dict]:
    with _tx(ctx, platform) as conn:
        rows = fetch_all(conn, f"""
            SELECT provider, op, count(*) AS calls,
                   count(*) FILTER (WHERE status = 'ok') AS ok,
                   count(*) FILTER (WHERE status NOT IN ('ok')) AS errors,
                   count(*) FILTER (WHERE cache_hit) AS cache_hits,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms,
                   sum(cost_micro_usd) AS cost
            FROM provider_call WHERE at > now() - interval '{days} days' GROUP BY provider, op ORDER BY provider, op""")
    for r in rows:
        r["error_rate"] = round(r["errors"] / r["calls"], 3) if r["calls"] else None
        r["cost_inr"] = _inr(r.pop("cost"))
    return rows


def errors(ctx: RequestContext, platform: bool, limit: int = 100) -> list[dict]:
    with _tx(ctx, platform) as conn:
        return fetch_all(conn, """SELECT id::text AS id, at, location, error_type, message, request_id,
                                  search_id::text AS search_id FROM error_event ORDER BY at DESC LIMIT :l""",
                         {"l": limit})


def search_timeline(ctx: RequestContext, search_id: uuid.UUID, platform: bool) -> list[dict]:
    """One ordered view of everything that happened in a search (the main debugging tool)."""
    with _tx(ctx, platform) as conn:
        events = fetch_all(conn, """SELECT at, kind, message, data FROM search_event WHERE search_id = :s""",
                           {"s": search_id})
        providers = fetch_all(conn, """SELECT at, provider, op, status, cache_hit, cost_micro_usd, latency_ms
                                       FROM provider_call WHERE search_id = :s""", {"s": search_id})
        llm = fetch_all(conn, """SELECT at, task, model, status, tokens_in, tokens_cached, tokens_out, cost_micro_usd,
                                 evidence_drops FROM llm_call WHERE search_id = :s""", {"s": search_id})
    timeline = [{"at": e["at"], "type": e["kind"], "message": e["message"], "data": e["data"]} for e in events]
    timeline += [{"at": p["at"], "type": "provider",
                  "message": f"{p['provider']}.{p['op']} {p['status']}{' (cache)' if p['cache_hit'] else ''}",
                  "data": {"cost_inr": _inr(p["cost_micro_usd"]), "latency_ms": p["latency_ms"]}} for p in providers]
    timeline += [{"at": a["at"], "type": "ai", "message": f"{a['task']} via {a['model']} {a['status']}",
                  "data": {"tokens_in": a["tokens_in"], "tokens_cached": a["tokens_cached"],
                           "tokens_out": a["tokens_out"], "cost_inr": _inr(a["cost_micro_usd"]),
                           "evidence_drops": a["evidence_drops"]}} for a in llm]
    return sorted(timeline, key=lambda t: t["at"])
