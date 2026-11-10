"""
Background worker process: `python -m app.cli worker`.

Loop: claim a queued search job -> run the pipeline -> mark done.
Failures are retried with back-off (search/jobs.py); after the last attempt the
search is marked FAILED and the error is visible in the admin Errors view.
Run several workers for more throughput - SKIP LOCKED keeps them apart.
"""
from __future__ import annotations

import time

from app.core.db import execute, system_tx
from app.core.logging import configure_logging, get_logger
from app.core.metrics import SEARCH_OUTCOMES
from app.modules.search import jobs
from app.modules.search.orchestrator import run_pipeline

log = get_logger("worker")


def process_one() -> bool:
    """Process a single job if one is waiting. Returns True if a job was processed."""
    job = jobs.claim_next()
    if not job:
        return False
    log.info("job_started", search_id=str(job["search_id"]), attempt=job["attempts"])
    try:
        status = run_pipeline(job["search_id"])
        jobs.mark_done(job["id"])
        log.info("job_finished", search_id=str(job["search_id"]), status=status)
    except Exception as exc:  # recorded by the orchestrator; decide retry here
        retry = jobs.mark_failed(job["id"], job["attempts"], f"{type(exc).__name__}: {exc}")
        if not retry:
            with system_tx() as conn:
                execute(conn, """UPDATE search SET status = 'FAILED', error = :e, updated_at = now()
                                 WHERE id = :id""", {"e": str(exc)[:500], "id": job["search_id"]})
            SEARCH_OUTCOMES.labels("FAILED").inc()
    return True


def run_pending() -> int:
    """Drain the queue synchronously (used by tests and the golden runner)."""
    n = 0
    while process_one():
        n += 1
    return n


def run_forever(idle_sleep_s: float = 1.0) -> None:
    configure_logging()
    log.info("worker_started")
    while True:
        if not process_one():
            time.sleep(idle_sleep_s)
