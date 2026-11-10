"""
Leads: what the customer sees, accepts, rates and exports.

Contact values live encrypted in the shared store; a lead only holds contact
ids. They are decrypted HERE, at read time, for the workspace that owns the
lead - so an erasure (which deletes the contact) shows up as "[erased]"
everywhere immediately.

REJECT rows never expose contact details (they were not delivered).
"""
from __future__ import annotations

import csv
import io
import uuid

from app.core.context import RequestContext
from app.core.db import as_json, execute, fetch_all, fetch_one, system_tx, tenant_tx
from app.core.errors import Conflict, NotFound
from app.core.ids import new_id
from app.core.security import lookup_hash
from app.modules.billing import service as billing
from app.modules.compliance.service import is_suppressed
from app.modules.knowledge import store

VISIBLE = ("pass", "degrade", "hold")


def _contacts(snapshot: dict) -> dict:
    ids = [v["id"] for k, v in snapshot.items() if isinstance(v, dict) and v.get("id")]
    with system_tx() as conn:
        values = store.contact_values(conn, ids)
    out = {}
    for kind in ("email", "phone", "whatsapp", "linkedin"):
        ref = snapshot.get(kind)
        if not ref:
            continue
        v = values.get(ref["id"])
        out[kind] = {"value": v["value"] if v else "[erased]",
                     "status": (v or {}).get("verify_status", "erased"),
                     "sources": (v or {}).get("sources", []),
                     "type": ref.get("type")}
    return out


def _row(r: dict, with_contacts: bool) -> dict:
    data = {
        "id": str(r["id"]), "search_id": str(r["search_id"]), "company": {
            "id": str(r["company_id"]), "name": r["company_name"], "domain": r["domain"], "city": r["city"],
            "size_band": r["size_band"], "employees": r["employees"], "industry": r["industry"]},
        "person": r["contact_snapshot"].get("person"), "path": r["path"], "fit": r["fit"],
        "final_score": r["final_score"], "confidence": r["row_confidence"], "decision": r["decision"],
        "reason_codes": r["reason_codes"], "reasons": r["reasons"], "opener": r["opener"],
        "billable": r["billable"], "accepted_from_hold": r["accepted_from_hold"],
        "delivered_at": r["delivered_at"].isoformat() if r["delivered_at"] else None,
    }
    if with_contacts and r["decision"] in VISIBLE:
        data["contacts"] = _contacts(r["contact_snapshot"])
    return data


_SELECT = """SELECT l.*, c.name AS company_name, c.domain, c.city, c.size_band, c.employees, c.industry
             FROM lead l JOIN company c ON c.id = l.company_id"""


def list_for_search(ctx: RequestContext, search_id: uuid.UUID, decision: str | None) -> list[dict]:
    with tenant_tx(ctx.workspace_id) as conn:
        params: dict = {"s": search_id}
        sql = _SELECT + " WHERE l.search_id = :s"
        if decision == "delivered":
            sql += " AND l.decision IN ('pass','degrade')"
        elif decision:
            sql += " AND l.decision = :d"
            params["d"] = decision
        rows = fetch_all(conn, sql + " ORDER BY l.final_score DESC", params)
    return [_row(r, with_contacts=True) for r in rows]


def get_lead(ctx: RequestContext, lead_id: uuid.UUID) -> dict:
    with tenant_tx(ctx.workspace_id) as conn:
        r = fetch_one(conn, _SELECT + " WHERE l.id = :id", {"id": lead_id})
        if not r:
            raise NotFound("Lead not found")
        checks = fetch_all(conn, "SELECT layer, check_code, passed, detail FROM judgment WHERE lead_id = :id ORDER BY at",
                           {"id": lead_id})
    with system_tx() as conn:
        evidence = {e["id"]: e for e in store.company_evidence(conn, r["company_id"], limit=30)}
    data = _row(r, with_contacts=True)
    data["checks"] = checks
    # Attach the text + URL of every evidence item a reason cites (clickable proof in the UI).
    data["evidence"] = {eid: evidence[eid] for reason in r["reasons"] for eid in reason.get("evidence_ids", [])
                        if eid in evidence}
    return data


def add_feedback(ctx: RequestContext, lead_id: uuid.UUID, verdict: str, reason: str | None) -> None:
    with tenant_tx(ctx.workspace_id) as conn:
        if not fetch_one(conn, "SELECT 1 AS x FROM lead WHERE id = :id", {"id": lead_id}):
            raise NotFound("Lead not found")
        execute(conn, """INSERT INTO feedback (id, workspace_id, lead_id, user_id, verdict, reason)
                         VALUES (:id, :ws, :l, :u, :v, :r)""",
                {"id": new_id(), "ws": ctx.workspace_id, "l": lead_id, "u": ctx.user_id, "v": verdict, "r": reason})


def accept_hold(ctx: RequestContext, lead_id: uuid.UUID) -> dict:
    """The user reviewed a HOLD row and wants it: charge 1 lead, claim its identities, deliver."""
    with tenant_tx(ctx.workspace_id) as conn:
        r = fetch_one(conn, _SELECT + " WHERE l.id = :id FOR UPDATE OF l", {"id": lead_id})
        if not r:
            raise NotFound("Lead not found")
        if r["decision"] != "hold":
            raise Conflict("Only rows in review (HOLD) can be accepted")
        contacts = _contacts(r["contact_snapshot"])
        identities = []
        if r["domain"]:
            identities.append(("business_domain", r["domain"]))
        if "linkedin" in contacts:
            identities.append(("person_linkedin", contacts["linkedin"]["value"]))
        for kind, value in identities:
            if fetch_one(conn, "SELECT 1 AS x FROM delivered_record WHERE identity_kind = :k AND identity_hmac = :h",
                         {"k": kind, "h": lookup_hash(kind, value)}):
                raise Conflict("This record was already delivered to your workspace", code="duplicate_delivered")
        billing.consume_one(conn, ctx.workspace_id, lead_id)
        for kind, value in identities:
            execute(conn, """INSERT INTO delivered_record (id, workspace_id, identity_kind, identity_hmac, lead_id,
                                                           person_id, company_id)
                             VALUES (:id, :ws, :k, :h, :l, :p, :c)""",
                    {"id": new_id(), "ws": ctx.workspace_id, "k": kind, "h": lookup_hash(kind, value),
                     "l": lead_id, "p": r["person_id"], "c": r["company_id"]})
        execute(conn, """UPDATE lead SET decision = 'pass', billable = true, accepted_from_hold = true,
                           delivered_at = now() WHERE id = :id""", {"id": lead_id})
        execute(conn, """UPDATE search SET delivered_count = delivered_count + 1, held_count = held_count - 1
                         WHERE id = :s""", {"s": r["search_id"]})
        execute(conn, """INSERT INTO audit_log (id, workspace_id, user_id, action, target)
                         VALUES (:id, :ws, :u, 'lead.accept_hold', :t)""",
                {"id": new_id(), "ws": ctx.workspace_id, "u": ctx.user_id, "t": str(lead_id)})
    return get_lead(ctx, lead_id)


def export_csv(ctx: RequestContext, search_id: uuid.UUID) -> str:
    """Delivered leads as CSV. Suppression is re-checked at export time."""
    leads = list_for_search(ctx, search_id, "delivered")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["company", "domain", "city", "size", "person", "title", "email", "email_status", "phone",
                "whatsapp", "linkedin", "fit", "score", "confidence", "reason_1", "reason_2", "reason_3", "opener"])
    removed = 0
    with tenant_tx(ctx.workspace_id) as conn:
        for l in leads:
            c = l.get("contacts", {})
            email = c.get("email", {}).get("value", "")
            phone = c.get("phone", {}).get("value", "")
            if email and is_suppressed(conn, "email", email):
                email, removed = "", removed + 1
            if phone and is_suppressed(conn, "phone", phone):
                phone, removed = "", removed + 1
            reasons = [r["text"] for r in l["reasons"]][:3] + ["", "", ""]
            w.writerow([l["company"]["name"], l["company"]["domain"], l["company"]["city"], l["company"]["size_band"],
                        (l["person"] or {}).get("name"), (l["person"] or {}).get("title"), email,
                        c.get("email", {}).get("status", ""), phone, c.get("whatsapp", {}).get("value", ""),
                        c.get("linkedin", {}).get("value", ""), l["fit"], l["final_score"], l["confidence"],
                        *reasons[:3], l["opener"] or ""])
        execute(conn, """INSERT INTO audit_log (id, workspace_id, user_id, action, target, meta)
                         VALUES (:id, :ws, :u, 'export.csv', :t, CAST(:m AS jsonb))""",
                {"id": new_id(), "ws": ctx.workspace_id, "u": ctx.user_id, "t": str(search_id),
                 "m": as_json({"rows": len(leads), "suppressed_removed": removed})})
    return buf.getvalue()
