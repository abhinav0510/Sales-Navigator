"""
Billing: plans and the lead-quota ledger.

Fairness rules this module enforces (SYSTEM_DESIGN 5.14):
* Leads are counted only when DELIVERED (PASS / DEGRADE / an accepted HOLD).
* Starting a search RESERVES its target from the monthly quota; when the search
  finishes, every unused unit is RELEASED back. Rejected rows cost nothing.

The ledger is append-only: balance = SUM(delta) for (workspace, period, kind).
Nothing is ever updated in place, so every unit can be audited.

Payments (Razorpay subscriptions, GST invoices) plug in at `change_plan()`;
v1 ships plan changes without a payment gateway (see docs/HANDOVER.md).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Connection

from app.core.config import load_yaml
from app.core.db import execute, fetch_all, fetch_one
from app.core.errors import NotFound, QuotaExceeded
from app.core.ids import new_id


def current_period() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def _ledger(conn: Connection, ws: uuid.UUID, delta: int, reason: str, ref: uuid.UUID | None = None,
            kind: str = "leads") -> None:
    execute(conn, """INSERT INTO quota_ledger (id, workspace_id, period, kind, delta, reason, ref_id)
                     VALUES (:id, :ws, :p, :k, :d, :r, :ref)""",
            {"id": new_id(), "ws": ws, "p": current_period(), "k": kind, "d": delta, "r": reason, "ref": ref})


def subscription(conn: Connection, ws: uuid.UUID) -> dict:
    row = fetch_one(conn, """SELECT s.*, p.name AS plan_name, p.monthly_leads, p.price_paise, p.seats
                             FROM subscription s JOIN plan p ON p.code = s.plan_code
                             WHERE s.workspace_id = :ws""", {"ws": ws})
    if not row:
        raise NotFound("No subscription for this workspace")
    return row


def ensure_period_grant(conn: Connection, ws: uuid.UUID) -> None:
    """Grant the plan's monthly leads the first time a period is touched (lazy monthly reset)."""
    period = current_period()
    exists = fetch_one(conn, """SELECT 1 AS x FROM quota_ledger WHERE workspace_id = :ws AND period = :p
                                AND reason = 'grant' LIMIT 1""", {"ws": ws, "p": period})
    if not exists:
        sub = subscription(conn, ws)
        _ledger(conn, ws, sub["monthly_leads"], "grant")
        execute(conn, "UPDATE subscription SET current_period = :p WHERE workspace_id = :ws", {"p": period, "ws": ws})


def balance(conn: Connection, ws: uuid.UUID, kind: str = "leads") -> int:
    ensure_period_grant(conn, ws)
    row = fetch_one(conn, """SELECT COALESCE(SUM(delta), 0) AS bal FROM quota_ledger
                             WHERE workspace_id = :ws AND period = :p AND kind = :k""",
                    {"ws": ws, "p": current_period(), "k": kind})
    return int(row["bal"])


def reserve(conn: Connection, ws: uuid.UUID, search_id: uuid.UUID, n: int) -> None:
    """Hold `n` leads for a search. Raises QuotaExceeded (HTTP 402) if not enough left."""
    # Lock the subscription row so two searches cannot reserve the same units.
    fetch_one(conn, "SELECT id FROM subscription WHERE workspace_id = :ws FOR UPDATE", {"ws": ws})
    available = balance(conn, ws)
    if available < n:
        raise QuotaExceeded(f"Only {available} leads left this month; this search needs {n}.",
                            extra={"available": available, "requested": n})
    _ledger(conn, ws, -n, "reserve", search_id)


def release(conn: Connection, ws: uuid.UUID, search_id: uuid.UUID, n: int) -> None:
    """Return unused reserved units when a search finishes or is cancelled."""
    if n > 0:
        _ledger(conn, ws, n, "release", search_id)


def consume_one(conn: Connection, ws: uuid.UUID, lead_id: uuid.UUID) -> None:
    """Charge one lead outside a reservation (a HOLD row the user accepts later)."""
    if balance(conn, ws) < 1:
        raise QuotaExceeded("No leads left this month to accept this row.")
    _ledger(conn, ws, -1, "accept_hold", lead_id)


def usage(conn: Connection, ws: uuid.UUID) -> dict:
    sub = subscription(conn, ws)
    bal = balance(conn, ws)
    rows = fetch_all(conn, """SELECT reason, SUM(delta) AS total FROM quota_ledger
                              WHERE workspace_id = :ws AND period = :p GROUP BY reason""",
                     {"ws": ws, "p": current_period()})
    return {"plan": sub["plan_code"], "plan_name": sub["plan_name"], "status": sub["status"],
            "monthly_leads": sub["monthly_leads"], "period": current_period(), "remaining": bal,
            "price_paise": sub["price_paise"], "ledger": {r["reason"]: int(r["total"]) for r in rows}}


def list_plans(conn: Connection) -> list[dict]:
    return fetch_all(conn, "SELECT * FROM plan ORDER BY price_paise")


def change_plan(conn: Connection, ws: uuid.UUID, plan_code: str) -> dict:
    """Switch plan. A real payment step (Razorpay) would precede this in production."""
    if plan_code not in load_yaml("plans")["plans"]:
        raise NotFound(f"Unknown plan '{plan_code}'")
    old = subscription(conn, ws)
    execute(conn, "UPDATE subscription SET plan_code = :c, status = 'active' WHERE workspace_id = :ws",
            {"c": plan_code, "ws": ws})
    new = subscription(conn, ws)
    # Top up the difference for the current period so an upgrade is usable immediately.
    extra = new["monthly_leads"] - old["monthly_leads"]
    if extra > 0:
        _ledger(conn, ws, extra, "adjust")
    return usage(conn, ws)
