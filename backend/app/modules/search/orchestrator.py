"""
Search orchestrator - runs one search end to end inside the worker.

    discovery  ->  scoring  ->  for each company, best score first:
                                    find person + contact
                                    Quality Judge
                                    deliver / hold / reject
                                stop when the target is delivered
               ->  complete: release unused quota, write the Quality Report

Backfill is built in: candidates are processed in score order, so every
rejected row is simply replaced by the next-best company - within the same
budget and without asking the user.

Resumable: each stage skips work already done (candidates exist, scores set,
leads written), so a retried or budget-resumed job continues where it stopped.
"""
from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError

from app.core.db import as_json, execute, fetch_all, fetch_one, system_tx, tenant_tx
from app.core.errors import BudgetExhausted
from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.metrics import DELIVERED_LEADS, QUALITY_DECISIONS, SEARCH_OUTCOMES
from app.core.security import lookup_hash
from app.modules.ai.service import AIScope, write_opener
from app.modules.billing import service as billing
from app.modules.contacts.service import ContactResult, find_contact
from app.modules.discovery.service import SearchContext, run_discovery
from app.modules.icp.schemas import IcpBody
from app.modules.knowledge import store
from app.modules.observability.recorder import record_error, record_search_event
from app.modules.providers.gateway import BudgetTracker, build_gateway
from app.modules.quality.service import Verdict, judge_row
from app.modules.scoring.service import score_candidates

log = get_logger("orchestrator")


def run_pipeline(search_id: uuid.UUID) -> str:
    """Run (or resume) a search. Returns the final status."""
    with system_tx() as conn:
        search = fetch_one(conn, "SELECT * FROM search WHERE id = :id", {"id": search_id})
        icp_row = fetch_one(conn, "SELECT * FROM icp WHERE id = :id", {"id": search["icp_id"]})
    if search["status"] != "RUNNING":
        return search["status"]
    ws = search["workspace_id"]
    icp = IcpBody(**icp_row["body"])
    budget = BudgetTracker(budget_paise=search["budget_paise"], spent_paise=float(search["spent_paise"]))
    gw = build_gateway(workspace_id=ws, search_id=search_id, budget=budget)

    def event(kind: str, msg: str, data: dict | None = None) -> None:
        record_search_event(workspace_id=ws, search_id=search_id, kind=kind, message=msg, data=data)

    try:
        # ---- 1. Discovery (skipped on resume if candidates already exist) ----
        with tenant_tx(ws) as conn:
            has_candidates = fetch_one(conn, "SELECT 1 AS x FROM candidate WHERE search_id = :s LIMIT 1",
                                       {"s": search_id})
        if not has_candidates:
            _set_stage(ws, search_id, "discovery", budget)
            ctx = SearchContext(workspace_id=ws, search_id=search_id, icp=icp,
                                scopes=search["geoscope"]["scopes"], target=search["target_count"],
                                include_previously_delivered=search["include_previously_delivered"], gw=gw)
            stats = run_discovery(ctx, search["plan"]["paths"])
            _merge_report(ws, search_id, {"discovery": {
                "found": stats.found, "kept": stats.kept, "dropped": stats.dropped,
                "injections_removed": stats.injections_removed,
                "unsafe_links_blocked": stats.unsafe_links_blocked}})
            event("stage", f"Discovery: {stats.found} found, {stats.kept} kept", {"dropped": stats.dropped})

        # ---- 2. Scoring (rule + AI fit, citation-enforced) --------------------
        _set_stage(ws, search_id, "scoring", budget)
        counts = score_candidates(workspace_id=ws, search_id=search_id, icp=icp)
        event("stage", f"Scoring: {counts}")

        # ---- 3. People, contacts, judge, deliver - best companies first ------
        _set_stage(ws, search_id, "enrich_and_judge", budget)
        _process_candidates(ws, search_id, search, icp, gw, event)

        # ---- 4. Complete ------------------------------------------------------
        return _complete(ws, search_id, budget, event)

    except BudgetExhausted:
        _persist_spend(ws, search_id, budget)
        with tenant_tx(ws) as conn:
            execute(conn, "UPDATE search SET status = 'PAUSED_BUDGET', updated_at = now() WHERE id = :id",
                    {"id": search_id})
        event("state", "Paused: search budget reached. Raise the budget to continue.")
        SEARCH_OUTCOMES.labels("PAUSED_BUDGET").inc()
        return "PAUSED_BUDGET"
    except Exception as exc:
        log.exception("search_failed", search_id=str(search_id))
        record_error(location="orchestrator.run_pipeline", error=exc, workspace_id=ws, search_id=search_id)
        raise


def _process_candidates(ws, search_id, search, icp: IcpBody, gw, event) -> None:
    with tenant_tx(ws) as conn:
        candidates = fetch_all(conn, """
            SELECT c.* FROM candidate c
            WHERE c.search_id = :s AND c.dropped_reason IS NULL AND c.final_score IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM lead l WHERE l.search_id = c.search_id AND l.company_id = c.company_id)
            ORDER BY c.final_score DESC, c.company_id""", {"s": search_id})

    for cand in candidates:
        with tenant_tx(ws) as conn:
            delivered = fetch_one(conn, "SELECT delivered_count FROM search WHERE id = :id", {"id": search_id})
        if delivered["delivered_count"] >= search["target_count"]:
            break
        with system_tx() as conn:
            company = store.get_company(conn, cand["company_id"])
            evidence = {e["id"]: e["snippet"] for e in store.company_evidence(conn, cand["company_id"], limit=20)}

        contact = find_contact(gw=gw, workspace_id=ws, search_id=search_id, icp=icp, company=company,
                               path=cand["path"])
        email_value = _email_value(contact)
        reasons = (cand["reasons"] or {}).get("items", [])
        verdict = judge_row(workspace_id=ws, search_id=search_id, icp=icp, company=company, path=cand["path"],
                            fit=cand["fit"], reasons=reasons, evidence=evidence, contact=contact,
                            email_value=email_value,
                            include_previously_delivered=search["include_previously_delivered"])
        opener = None
        if verdict.decision in ("pass", "degrade", "hold") and verdict.kept_reasons:
            first_name = (contact.full_name or "there").split()[0]
            best = _best_evidence(verdict.kept_reasons, evidence)
            opener = write_opener(AIScope(ws, search_id), icp, first_name=first_name, company=company["name"],
                                  evidence_snippet=best)
        decision = _deliver(ws, search_id, cand, company, contact, verdict, opener)
        QUALITY_DECISIONS.labels(decision, cand["path"]).inc()
        event("judge", f"{company['name']}: {decision.upper()} (confidence {verdict.confidence:.2f})",
              {"codes": verdict.reason_codes})


def _email_value(contact: ContactResult) -> str | None:
    if not contact.email:
        return None
    with system_tx() as conn:
        return store.contact_values(conn, [contact.email["id"]]).get(contact.email["id"], {}).get("value")


def _best_evidence(reasons: list[dict], evidence: dict[str, str]) -> str:
    """Prefer the evidence behind the LAST reason (triggers / pains are the most specific)."""
    ids = [eid for r in reversed(reasons) for eid in r.get("evidence_ids", [])]
    return evidence.get(ids[0], "") if ids else ""


def _deliver(ws, search_id, cand, company, contact: ContactResult, verdict: Verdict, opener: str | None) -> str:
    """Write the lead row. For PASS/DEGRADE also claim its identities in the dedupe ledger."""
    decision = verdict.decision
    lead_id = new_id()
    failed_fields = {c.code for c in verdict.checks if c.layer == "field" and not c.passed}
    snapshot = {
        "email": contact.email if contact.email and "email_domain_matches_company" not in failed_fields else None,
        "phone": contact.phone, "whatsapp": contact.whatsapp, "linkedin": contact.linkedin,
        "person": {"name": contact.full_name, "title": contact.title},
    }
    with tenant_tx(ws) as conn:
        if decision in ("pass", "degrade"):
            try:
                with conn.begin_nested():               # savepoint: all identities or none
                    for kind, value in verdict.identities:
                        execute(conn, """INSERT INTO delivered_record (id, workspace_id, identity_kind, identity_hmac,
                                                                      lead_id, person_id, company_id)
                                         VALUES (:id, :ws, :k, :h, :l, :p, :c)""",
                                {"id": new_id(), "ws": ws, "k": kind, "h": lookup_hash(kind, value),
                                 "l": lead_id, "p": contact.person_id, "c": company["id"]})
            except IntegrityError:
                # A concurrent search delivered the same record first: never deliver twice.
                decision = "reject"
                verdict.reason_codes = ["duplicate_delivered"]
        billable = decision in ("pass", "degrade")
        execute(conn, """INSERT INTO lead (id, workspace_id, search_id, company_id, person_id, path, final_score, fit,
                                           reasons, opener, contact_snapshot, row_confidence, decision, reason_codes,
                                           billable, delivered_at)
                         VALUES (:id, :ws, :s, :c, :p, :path, :fs, :fit, CAST(:r AS jsonb), :op, CAST(:snap AS jsonb),
                                 :conf, :d, :codes, :bill, CASE WHEN :bill THEN now() END)""",
                {"id": lead_id, "ws": ws, "s": search_id, "c": company["id"], "p": contact.person_id,
                 "path": cand["path"], "fs": cand["final_score"], "fit": cand["fit"],
                 "r": as_json(verdict.kept_reasons), "op": opener, "snap": as_json(snapshot),
                 "conf": verdict.confidence, "d": decision, "codes": verdict.reason_codes, "bill": billable})
        for chk in verdict.checks:
            execute(conn, """INSERT INTO judgment (id, workspace_id, lead_id, layer, check_code, passed, detail)
                             VALUES (:id, :ws, :l, :layer, :code, :ok, CAST(:d AS jsonb))""",
                    {"id": new_id(), "ws": ws, "l": lead_id, "layer": chk.layer, "code": chk.code,
                     "ok": chk.passed, "d": as_json(chk.detail)})
        column = {"pass": "delivered_count", "degrade": "delivered_count", "hold": "held_count",
                  "reject": "rejected_count"}[decision]
        execute(conn, f"UPDATE search SET {column} = {column} + 1, updated_at = now() WHERE id = :id",
                {"id": search_id})
    if decision in ("pass", "degrade"):
        DELIVERED_LEADS.labels("all").inc()
    return decision


def _complete(ws, search_id, budget: BudgetTracker, event) -> str:
    _persist_spend(ws, search_id, budget)
    with tenant_tx(ws) as conn:
        search = fetch_one(conn, "SELECT * FROM search WHERE id = :id", {"id": search_id})
        unused = max(0, search["quota_reserved"] - search["delivered_count"])
        billing.release(conn, ws, search_id, unused)
        report = _quality_report(conn, search)
        execute(conn, """UPDATE search SET status = 'COMPLETED', stage = 'done', quota_reserved = quota_reserved - :u,
                           quality_report = CAST(:q AS jsonb), completed_at = now(), updated_at = now()
                         WHERE id = :id""", {"u": unused, "q": as_json(report), "id": search_id})
    partial = search["delivered_count"] < search["target_count"]
    event("state", f"Completed: delivered {search['delivered_count']} of {search['target_count']}"
                   + (" (candidate pool exhausted)" if partial else ""), {"released_quota": unused})
    SEARCH_OUTCOMES.labels("COMPLETED").inc()
    return "COMPLETED"


def _quality_report(conn, search: dict) -> dict:
    """Everything the user (and the operator) needs to see why the result looks the way it does."""
    sid = search["id"]
    dropped = {r["dropped_reason"]: r["n"] for r in fetch_all(
        conn, "SELECT dropped_reason, count(*) AS n FROM candidate WHERE search_id = :s AND dropped_reason IS NOT NULL "
              "GROUP BY 1", {"s": sid})}
    leads = fetch_all(conn, "SELECT decision, row_confidence, reason_codes, contact_snapshot FROM lead WHERE search_id = :s",
                      {"s": sid})
    delivered = [l for l in leads if l["decision"] in ("pass", "degrade")]
    codes: dict[str, int] = {}
    for l in leads:
        if l["decision"] in ("hold", "reject"):
            for c in l["reason_codes"]:
                codes[c] = codes.get(c, 0) + 1
    verified = sum(1 for l in delivered if (l["contact_snapshot"].get("email") or {}).get("status") == "valid")
    ai = fetch_one(conn, "SELECT count(*) AS calls, COALESCE(sum(cost_micro_usd),0) AS cost, "
                         "COALESCE(sum(tokens_cached),0) AS cached FROM llm_call WHERE search_id = :s", {"s": sid})
    prev = search["quality_report"] or {}
    return {
        "discovery": prev.get("discovery", {}),
        "dropped_before_judge": dropped,
        "decisions": {d: sum(1 for l in leads if l["decision"] == d) for d in ("pass", "degrade", "hold", "reject")},
        "hold_and_reject_reasons": codes,
        "avg_confidence_delivered": round(sum(l["row_confidence"] for l in delivered) / len(delivered), 3)
        if delivered else None,
        "verified_email_rate": round(verified / len(delivered), 3) if delivered else None,
        "spent_paise": int(search["spent_paise"]),
        "cost_per_delivered_lead_paise": round(search["spent_paise"] / len(delivered)) if delivered else None,
        "ai": {"calls": ai["calls"], "cost_micro_usd": int(ai["cost"]), "cached_tokens": int(ai["cached"])},
    }


def _merge_report(ws, search_id, data: dict) -> None:
    """Add a section to the search's Quality Report as soon as it is known (visible while running)."""
    with tenant_tx(ws) as conn:
        execute(conn, """UPDATE search SET quality_report = COALESCE(quality_report, '{}'::jsonb) || CAST(:d AS jsonb)
                         WHERE id = :id""", {"d": as_json(data), "id": search_id})


def _set_stage(ws, search_id, stage: str, budget: BudgetTracker) -> None:
    _persist_spend(ws, search_id, budget)
    with tenant_tx(ws) as conn:
        execute(conn, "UPDATE search SET stage = :st, updated_at = now() WHERE id = :id", {"st": stage, "id": search_id})


def _persist_spend(ws, search_id, budget: BudgetTracker) -> None:
    """Spend = provider calls (tracked live) + AI calls (from the llm_call ledger)."""
    with tenant_tx(ws) as conn:
        ai = fetch_one(conn, "SELECT COALESCE(sum(cost_micro_usd), 0) AS c FROM llm_call WHERE search_id = :s",
                       {"s": search_id})
        ai_paise = budget.cost_paise(int(ai["c"]))
        execute(conn, "UPDATE search SET spent_paise = :p WHERE id = :id",
                {"p": int(round(budget.spent_paise + ai_paise)), "id": search_id})
