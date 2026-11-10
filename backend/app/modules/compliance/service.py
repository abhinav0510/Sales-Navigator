"""
Compliance: suppression lists and erasure requests (DPDP / GDPR).

Suppression values are stored only as keyed hashes, so the opt-out list itself
holds no readable personal data. Two levels:
    workspace suppression  - a customer's own do-not-contact list
    global suppression     - erasure requests; applies to EVERY workspace

Checks run before contacts are stored on a lead AND again at export time.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Connection

from app.core.db import execute, fetch_all, fetch_one, system_tx, tenant_tx
from app.core.ids import new_id
from app.core.security import lookup_hash
from app.modules.knowledge.normalize import phone_e164

KINDS = ("email", "phone", "domain")


def _normalize(kind: str, value: str) -> str | None:
    value = value.strip().lower()
    if kind == "phone":
        return phone_e164(value)
    if kind == "domain":
        return value.removeprefix("www.")
    return value if "@" in value else None


def add_suppressions(workspace_id: uuid.UUID, kind: str, values: list[str], reason: str = "customer_list") -> int:
    added = 0
    with tenant_tx(workspace_id) as conn:
        for v in values:
            norm = _normalize(kind, v)
            if not norm:
                continue
            added += execute(conn, """INSERT INTO suppression (id, workspace_id, kind, value_hmac, reason)
                                      VALUES (:id, :ws, :k, :h, :r) ON CONFLICT DO NOTHING""",
                             {"id": new_id(), "ws": workspace_id, "k": kind, "h": lookup_hash(kind, norm), "r": reason})
    return added


def is_suppressed(conn: Connection, kind: str, value: str) -> bool:
    """True if `value` is on this workspace's list or the global list (RLS shows both)."""
    norm = _normalize(kind, value)
    if not norm:
        return False
    return bool(fetch_one(conn, "SELECT 1 AS x FROM suppression WHERE kind = :k AND value_hmac = :h",
                          {"k": kind, "h": lookup_hash(kind, norm)}))


def list_suppressions(workspace_id: uuid.UUID) -> list[dict]:
    with tenant_tx(workspace_id) as conn:
        return fetch_all(conn, """SELECT id::text AS id, kind, reason, at,
                                  (workspace_id IS NULL) AS is_global FROM suppression ORDER BY at DESC LIMIT 500""")


def erase_person_everywhere(email: str) -> dict:
    """
    Erasure request: delete the contact value from the shared store, mark the
    person erased, and add a GLOBAL suppression so no workspace gets it again.
    """
    norm = _normalize("email", email)
    if not norm:
        return {"erased": 0}
    h = lookup_hash("work_email", norm)
    with system_tx() as conn:
        rows = fetch_all(conn, "SELECT id, person_id FROM contact_point WHERE kind = 'work_email' AND value_hmac = :h",
                         {"h": h})
        for r in rows:
            execute(conn, "DELETE FROM contact_point WHERE id = :id", {"id": r["id"]})
            if r["person_id"]:
                execute(conn, "UPDATE person SET erased = true WHERE id = :p", {"p": r["person_id"]})
        execute(conn, """INSERT INTO suppression (id, workspace_id, kind, value_hmac, reason)
                         VALUES (:id, NULL, 'email', :h, 'erasure_request') ON CONFLICT DO NOTHING""",
                {"id": new_id(), "h": lookup_hash("email", norm)})
    return {"erased": len(rows)}
