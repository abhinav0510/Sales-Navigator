"""
Background job queue on PostgreSQL (no Redis/Celery needed in v1).

Why Postgres: one less service for a single developer to run. Workers claim
jobs with `FOR UPDATE SKIP LOCKED`, so any number of worker processes can run
side by side without ever taking the same job twice.

Lifecycle:  queued -> running -> done
                            \\-> queued again (retry, with back-off) -> failed after 3 attempts
A job stuck in `running` for 15 minutes (crashed worker) is put back in the queue.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Connection

from app.core.db import execute, fetch_one, system_tx
from app.core.ids import new_id

MAX_ATTEMPTS = 3
STALE_AFTER = "15 minutes"


def enqueue(conn: Connection, workspace_id: uuid.UUID, search_id: uuid.UUID) -> None:
    """Called inside the API's tenant transaction, so the job exists only if the request commits."""
    execute(conn, "INSERT INTO search_job (id, workspace_id, search_id) VALUES (:id, :ws, :s)",
            {"id": new_id(), "ws": workspace_id, "s": search_id})


def claim_next() -> dict | None:
    with system_tx() as conn:
        execute(conn, f"""UPDATE search_job SET status = 'queued', locked_at = NULL
                          WHERE status = 'running' AND locked_at < now() - interval '{STALE_AFTER}'""")
        return fetch_one(conn, """
            UPDATE search_job SET status = 'running', locked_at = now(), attempts = attempts + 1
            WHERE id = (SELECT id FROM search_job WHERE status = 'queued' AND run_after <= now()
                        ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
            RETURNING id, workspace_id, search_id, attempts""")


def mark_done(job_id: uuid.UUID) -> None:
    with system_tx() as conn:
        execute(conn, "UPDATE search_job SET status = 'done', locked_at = NULL WHERE id = :id", {"id": job_id})


def mark_failed(job_id: uuid.UUID, attempts: int, error: str) -> bool:
    """Re-queue with back-off, or give up. Returns True if it will be retried."""
    retry = attempts < MAX_ATTEMPTS
    with system_tx() as conn:
        execute(conn, """UPDATE search_job SET status = :st, locked_at = NULL, last_error = :e,
                           run_after = now() + make_interval(secs => :delay) WHERE id = :id""",
                {"st": "queued" if retry else "failed", "e": error[:2000], "delay": 30 * attempts, "id": job_id})
    return retry
