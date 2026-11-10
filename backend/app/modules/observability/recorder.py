"""
Observability recorder - the single place that writes the operator's ledgers.

    provider_call  every data-provider call (cost, cache hit, latency, status)
    llm_call       every AI call (model, tokens incl. cached, cost, fallback, schema)
    search_event   the per-search timeline shown in the admin "Search timeline" view
    error_event    handled + unhandled errors (the admin "Errors" view)

All writes use the system role because they happen in the background worker,
on behalf of a workspace that is identified by the `workspace_id` column.
A failure to record must NEVER break the pipeline, so every write is wrapped.
"""
from __future__ import annotations

import uuid
from typing import Any

from app.core.db import as_json, execute, system_tx
from app.core.ids import new_id
from app.core.logging import get_logger, redact_text

log = get_logger("recorder")


def record_provider_call(*, workspace_id: uuid.UUID | None, search_id: uuid.UUID | None,
                         provider: str, op: str, cost_micro_usd: int, cache_hit: bool,
                         status: str, latency_ms: int) -> None:
    _safe_write(
        """INSERT INTO provider_call (id, workspace_id, search_id, provider, op, cost_micro_usd,
                                      cache_hit, status, latency_ms)
           VALUES (:id, :ws, :s, :p, :op, :c, :hit, :st, :lat)""",
        {"id": new_id(), "ws": workspace_id, "s": search_id, "p": provider, "op": op,
         "c": cost_micro_usd, "hit": cache_hit, "st": status, "lat": latency_ms},
    )


def record_llm_call(*, workspace_id: uuid.UUID | None, search_id: uuid.UUID | None, task: str,
                    model: str, prompt_version: str, tokens_in: int, tokens_cached: int,
                    tokens_out: int, cost_micro_usd: int, latency_ms: int, status: str,
                    retries: int = 0, fallback_used: bool = False, schema_valid: bool = True,
                    evidence_drops: int = 0) -> None:
    _safe_write(
        """INSERT INTO llm_call (id, workspace_id, search_id, task, model, prompt_version, tokens_in,
                                 tokens_cached, tokens_out, cost_micro_usd, latency_ms, status, retries,
                                 fallback_used, schema_valid, evidence_drops)
           VALUES (:id, :ws, :s, :task, :model, :pv, :ti, :tc, :to, :c, :lat, :st, :r, :fb, :sv, :ed)""",
        {"id": new_id(), "ws": workspace_id, "s": search_id, "task": task, "model": model,
         "pv": prompt_version, "ti": tokens_in, "tc": tokens_cached, "to": tokens_out,
         "c": cost_micro_usd, "lat": latency_ms, "st": status, "r": retries, "fb": fallback_used,
         "sv": schema_valid, "ed": evidence_drops},
    )


def record_search_event(*, workspace_id: uuid.UUID, search_id: uuid.UUID, kind: str,
                        message: str, data: dict[str, Any] | None = None) -> None:
    _safe_write(
        """INSERT INTO search_event (id, workspace_id, search_id, kind, message, data)
           VALUES (:id, :ws, :s, :k, :m, CAST(:d AS jsonb))""",
        {"id": new_id(), "ws": workspace_id, "s": search_id, "k": kind,
         "m": redact_text(message), "d": as_json(data or {})},
    )


def record_error(*, location: str, error: BaseException, workspace_id: uuid.UUID | None = None,
                 search_id: uuid.UUID | None = None, request_id: str | None = None) -> None:
    _safe_write(
        """INSERT INTO error_event (id, workspace_id, search_id, request_id, location, error_type, message)
           VALUES (:id, :ws, :s, :rid, :loc, :t, :m)""",
        {"id": new_id(), "ws": workspace_id, "s": search_id, "rid": request_id, "loc": location,
         "t": type(error).__name__, "m": redact_text(str(error))[:2000]},
    )


def _safe_write(sql: str, params: dict[str, Any]) -> None:
    try:
        with system_tx() as conn:
            execute(conn, sql, params)
    except Exception as exc:  # recording must never break the caller
        log.warning("observability_write_failed", error=str(exc))
