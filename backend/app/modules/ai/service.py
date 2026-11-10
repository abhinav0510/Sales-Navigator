"""
AI service - the facade every pipeline stage uses for AI work.

    build_icp(...)      -> IcpBody
    score_fit(...)      -> list[FitJudgment]   (batched, citations enforced)
    judge_reasons(...)  -> JudgeVerdict | None (None = no judge available -> HOLD)
    write_opener(...)   -> str | None

It picks the implementation from SN_LLM_MODE (offline rules or Claude), applies
the guardrails identically in both modes, and records one llm_call row per
call so the operator's AI dashboard shows tokens, cost, fallbacks and failures.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from app.core.config import get_settings, load_yaml
from app.core.metrics import LLM_CALLS, LLM_TOKENS
from app.modules.ai import offline, prompts
from app.modules.ai.guardrails import enforce_citations
from app.modules.ai.schemas import FitJudgment, JudgeVerdict
from app.modules.ai.wire import (FitBatchWire, IcpWire, JudgeWire, OpenerWire, to_fit, to_icp,
                                 to_judge, to_opener)
from app.modules.icp.schemas import IcpBody
from app.modules.observability.recorder import record_llm_call

OFFLINE_MODEL = "offline-rules"


@dataclass
class AIScope:
    """Who the AI work is for - used only for recording."""

    workspace_id: uuid.UUID | None = None
    search_id: uuid.UUID | None = None


def _claude_mode() -> bool:
    return get_settings().llm_mode == "claude"


def _record(scope: AIScope, task: str, *, model: str, status: str, latency_ms: int,
            tokens_in: int = 0, tokens_cached: int = 0, tokens_out: int = 0, cost: int = 0,
            fallback: bool = False, retries: int = 0, evidence_drops: int = 0,
            schema_valid: bool = True) -> None:
    version = load_yaml("llm")["tasks"].get(task, {}).get("prompt_version", f"{task}/v1")
    LLM_CALLS.labels(task, model, status).inc()
    for kind, n in (("in", tokens_in), ("cached", tokens_cached), ("out", tokens_out)):
        if n:
            LLM_TOKENS.labels(task, model, kind).inc(n)
    record_llm_call(workspace_id=scope.workspace_id, search_id=scope.search_id, task=task, model=model,
                    prompt_version=version, tokens_in=tokens_in, tokens_cached=tokens_cached,
                    tokens_out=tokens_out, cost_micro_usd=cost, latency_ms=latency_ms, status=status,
                    retries=retries, fallback_used=fallback, schema_valid=schema_valid,
                    evidence_drops=evidence_drops)


# ---------------------------------------------------------------------------
def build_icp(scope: AIScope, *, business_name: str, description: str, website_text: str,
              seeds: list[str]) -> IcpBody:
    started = time.monotonic()
    if not _claude_mode():
        body = offline.build_icp(business_name, description, website_text, seeds)
        _record(scope, "icp", model=OFFLINE_MODEL, status="ok", latency_ms=int((time.monotonic() - started) * 1000))
        return body

    from app.modules.ai.claude import AIUnavailable, run_task

    try:
        res = run_task("icp", system=prompts.cached_system(prompts.ICP_SYSTEM, None),
                       user=prompts.icp_user_message(business_name, description, website_text, seeds),
                       output_model=IcpWire)
    except AIUnavailable:
        _record(scope, "icp", model="none", status="error", latency_ms=int((time.monotonic() - started) * 1000))
        # Degrade gracefully: the rules-based draft is still editable by the user.
        return offline.build_icp(business_name, description, website_text, seeds)
    body = to_icp(res.output)  # type: ignore[arg-type]
    body.exclusions = sorted({*body.exclusions, *(s.strip().lower() for s in seeds if s.strip())})
    _record(scope, "icp", model=res.model, status="ok", latency_ms=res.latency_ms, tokens_in=res.tokens_in,
            tokens_cached=res.tokens_cached, tokens_out=res.tokens_out, cost=res.cost_micro_usd,
            fallback=res.fallback_used, retries=res.attempts - 1)
    return body


# ---------------------------------------------------------------------------
def score_fit(scope: AIScope, icp: IcpBody, cards: list[dict]) -> list[FitJudgment]:
    """
    Score companies against the ICP. `cards` = [{company_id, name, ..., evidence:[{id,kind,snippet}]}].
    Guardrail: every reason must cite an evidence id from THAT company's card.
    """
    started = time.monotonic()
    judgments: list[FitJudgment] = []
    meta = dict(model=OFFLINE_MODEL, status="ok", tokens_in=0, tokens_cached=0, tokens_out=0,
                cost=0, fallback=False, retries=0)

    if not _claude_mode():
        judgments = [offline.score_fit(icp, c) for c in cards]
    else:
        from app.modules.ai.claude import AIUnavailable, run_task

        try:
            res = run_task("fit", system=prompts.cached_system(prompts.FIT_SYSTEM, icp),
                           user=prompts.fit_user_message(cards), output_model=FitBatchWire)
            judgments = [to_fit(j) for j in res.output.judgments]  # type: ignore[attr-defined]
            meta.update(model=res.model, tokens_in=res.tokens_in, tokens_cached=res.tokens_cached,
                        tokens_out=res.tokens_out, cost=res.cost_micro_usd, fallback=res.fallback_used,
                        retries=res.attempts - 1)
        except AIUnavailable:
            meta.update(model="none", status="error")
            judgments = []          # caller falls back to rule score only

    # Guardrail: citation enforcement (identical in both modes).
    by_id = {c["company_id"]: {e["id"] for e in c["evidence"]} for c in cards}
    drops = 0
    for j in judgments:
        j.reasons, dropped = enforce_citations(j.reasons, by_id.get(j.company_id, set()))
        drops += dropped
    _record(scope, "fit", latency_ms=int((time.monotonic() - started) * 1000), evidence_drops=drops, **meta)
    return judgments


# ---------------------------------------------------------------------------
def judge_reasons(scope: AIScope, icp: IcpBody, *, fit: str, reasons: list[dict],
                  evidence: dict[str, str]) -> JudgeVerdict | None:
    """Independent check of a lead's reasons. Returns None if no judge-tier model answered."""
    started = time.monotonic()
    if not _claude_mode():
        verdict = offline.judge(fit, reasons, evidence)
        _record(scope, "judge", model=OFFLINE_MODEL, status="ok", latency_ms=int((time.monotonic() - started) * 1000))
        return verdict

    from app.modules.ai.claude import AIUnavailable, run_task

    try:
        res = run_task("judge", system=prompts.cached_system(prompts.JUDGE_SYSTEM, icp),
                       user=prompts.judge_user_message(fit, reasons, evidence), output_model=JudgeWire)
    except AIUnavailable:
        _record(scope, "judge", model="none", status="error", latency_ms=int((time.monotonic() - started) * 1000))
        return None             # no judge -> the row waits in HOLD (never delivered unjudged)
    _record(scope, "judge", model=res.model, status="ok", latency_ms=res.latency_ms, tokens_in=res.tokens_in,
            tokens_cached=res.tokens_cached, tokens_out=res.tokens_out, cost=res.cost_micro_usd,
            fallback=res.fallback_used, retries=res.attempts - 1)
    return to_judge(res.output)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
def write_opener(scope: AIScope, icp: IcpBody, *, first_name: str, company: str,
                 evidence_snippet: str) -> str | None:
    started = time.monotonic()
    if not _claude_mode():
        topic = icp.pains[0] if icp.pains else None
        text = offline.opener(first_name, company, evidence_snippet, topic).text
        _record(scope, "opener", model=OFFLINE_MODEL, status="ok", latency_ms=int((time.monotonic() - started) * 1000))
        return text

    from app.modules.ai.claude import AIUnavailable, run_task

    user = (f"PERSON: {first_name} at {company}\nEVIDENCE:\n"
            + prompts.fence("opener-evidence", evidence_snippet))
    try:
        res = run_task("opener", system=prompts.cached_system(prompts.OPENER_SYSTEM, icp), user=user,
                       output_model=OpenerWire)
    except AIUnavailable:
        _record(scope, "opener", model="none", status="error", latency_ms=int((time.monotonic() - started) * 1000))
        return None             # deliver without an opener
    _record(scope, "opener", model=res.model, status="ok", latency_ms=res.latency_ms, tokens_in=res.tokens_in,
            tokens_cached=res.tokens_cached, tokens_out=res.tokens_out, cost=res.cost_micro_usd,
            fallback=res.fallback_used, retries=res.attempts - 1)
    return to_opener(res.output).text  # type: ignore[arg-type]
