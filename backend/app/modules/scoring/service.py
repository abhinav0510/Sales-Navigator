"""
Company scoring = rule score (0-60, explainable) + AI fit score (0-40).

Rule score (config/scoring_weights.yaml):
    industry_match   ICP industry words appear in the company's evidence
    size_match       team size inside the ICP's target sizes (partial if adjacent)
    geo_precision    inside the search region (always true here - gated earlier)
    tech_match       uses a platform the ICP targets (Shopify, WooCommerce, ...)
    persona_available  neutral half-score until people are found
    trigger_signals  a buying trigger appears (e.g. hiring for operations)
    website_quality  site answered with more than one page

AI fit (ai.service.score_fit): fit class + 0-40 + reasons that MUST cite evidence.
Companies with fit "no" or final < company_threshold are dropped (no people
or contact lookups are spent on them - two-stage filtering).
"""
from __future__ import annotations

import uuid

from app.core.config import load_yaml
from app.core.db import as_json, execute, fetch_all, system_tx, tenant_tx
from app.modules.ai.service import AIScope, score_fit
from app.modules.ai.text_match import mentions
from app.modules.icp.schemas import IcpBody
from app.modules.knowledge import store

_ADJACENT = {("micro", "smb"), ("smb", "mid"), ("mid", "enterprise")}


def rule_score(icp: IcpBody, company: dict, evidence: list[dict]) -> tuple[int, dict]:
    w = load_yaml("scoring_weights")["rule_weights"]
    text = " ".join(e["snippet"] for e in evidence).lower()
    parts = {
        "industry_match": w["industry_match"] if any(mentions(text, k) for k in icp.industry_keywords) else 0,
        "geo_precision": w["geo_precision"],
        "tech_match": w["tech_match"] if any(
            mentions(text, t) or t in (company.get("tech") or []) for t in icp.technologies) else 0,
        "persona_available": w["persona_available"] // 2,
        "trigger_signals": w["trigger_signals"] if any(mentions(text, t) for t in icp.triggers) else 0,
        "website_quality": w["website_quality"] if len(evidence) >= 2 else 0,
    }
    size = company.get("size_band")
    if not icp.target_size or size in icp.target_size:
        parts["size_match"] = w["size_match"]
    elif any((size, t) in _ADJACENT or (t, size) in _ADJACENT for t in icp.target_size):
        parts["size_match"] = w["size_match"] // 3
    else:
        parts["size_match"] = 0
    return sum(parts.values()), parts


def score_candidates(*, workspace_id: uuid.UUID, search_id: uuid.UUID, icp: IcpBody) -> dict:
    """Score every kept, not-yet-scored candidate of a search. Returns counts."""
    with tenant_tx(workspace_id) as conn:
        rows = fetch_all(conn, """SELECT id, company_id FROM candidate
                                  WHERE search_id = :s AND dropped_reason IS NULL AND final_score IS NULL""",
                         {"s": search_id})
    if not rows:
        return {"scored": 0}

    cards, by_company = [], {}
    with system_tx() as conn:
        for r in rows:
            company = store.get_company(conn, r["company_id"])
            evidence = store.company_evidence(conn, r["company_id"])
            by_company[str(r["company_id"])] = (r, company, evidence)
            cards.append({"company_id": str(r["company_id"]), "name": company["name"],
                          "industry": company["industry"], "city": company["city"],
                          "size_band": company["size_band"], "employees": company["employees"],
                          "tech": company["tech"], "evidence": evidence})

    batch = load_yaml("llm")["fit_batch_size"]
    judgments = {}
    scope = AIScope(workspace_id=workspace_id, search_id=search_id)
    for i in range(0, len(cards), batch):
        for j in score_fit(scope, icp, cards[i:i + batch]):
            judgments[j.company_id] = j

    threshold = load_yaml("scoring_weights")["company_threshold"]
    kept = dropped = 0
    with tenant_tx(workspace_id) as conn:
        for cid, (row, company, evidence) in by_company.items():
            rscore, parts = rule_score(icp, company, evidence)
            j = judgments.get(cid)
            # If the AI could not score (all models down), fall back to the rule score alone.
            llm = j.score_0_40 if j else 0
            fit = j.fit if j else ("medium" if rscore >= 45 else "weak")
            final = rscore + llm
            reason = None
            if fit == "no":
                reason = "icp_mismatch"
            elif final < threshold:
                reason = "below_threshold"
            reasons = [r.model_dump() for r in j.reasons] if j else []
            execute(conn, """UPDATE candidate SET rule_score = :rs, llm_score = :ls, final_score = :fs, fit = :fit,
                               reasons = CAST(:reasons AS jsonb), dropped_reason = :dr WHERE id = :id""",
                    {"rs": rscore, "ls": llm, "fs": final, "fit": fit,
                     "reasons": as_json({"items": reasons, "rule_parts": parts, "risks": j.risks if j else []}),
                     "dr": reason, "id": row["id"]})
            kept += reason is None
            dropped += reason is not None
    return {"scored": len(by_company), "kept": kept, "dropped": dropped}
