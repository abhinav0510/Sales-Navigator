"""
Search Planner - decides WHICH discovery path(s) a search uses, before any money
is spent (SYSTEM_DESIGN 5.2a, LLD 6.3).

    features (ICP + region + available sources)
        -> rules from config/planner_rules.yaml (safe expressions, never eval)
        -> path shares, normalised to 1.0
        -> confidence: clear winner? ambiguous industry?
        -> confidence < 0.6  => ONE clarifying question for the user
        -> cost estimate for the chosen plan

The plan is shown to the user in plain language and can be overridden.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from simpleeval import EvalWithCompoundTypes

from app.core.config import get_settings, load_yaml
from app.modules.icp.schemas import IcpBody
from app.modules.providers.base import UNIT_COST_MICRO_USD

PATH_LABELS = {
    "B2B_COMPANY": "Companies with teams, reached via their decision-makers",
    "LOCAL_BUSINESS": "Local, owner-run businesses from place data",
    "LOOKALIKE": "Companies similar to your existing customers",
    "SIGNAL_LED": "Companies showing buying signals (hiring, launches)",
    "B2B2C": "Businesses that reach your consumers (no consumer personal data)",
    "AUDIENCE_INSIGHT": "Market report only - no contacts",
}
# What each path needs from the provider layer.
PATH_SOURCES = {
    "B2B_COMPANY": {"web_search"}, "LOOKALIKE": {"web_search"}, "SIGNAL_LED": {"web_search"},
    "LOCAL_BUSINESS": {"place"}, "B2B2C": {"place"}, "AUDIENCE_INSIGHT": set(),
}


@dataclass
class SearchPlan:
    paths: list[dict]                       # [{path, share, label}]
    confidence: float
    rationale: list[str]
    question: dict | None = None            # {text, options:[{label, path}]}
    estimate: dict = field(default_factory=dict)
    decided_by: str = "rules"

    def to_dict(self) -> dict:
        return asdict(self)


def _features(icp: IcpBody, seeds: list[str], available: set[str]) -> dict:
    trigger_strength = min(1.0, 0.3 * len(icp.triggers)) if icp.triggers else 0.0
    return {
        "seller_mode": icp.seller_mode,
        "target_size": set(icp.target_size),
        "local_ratio": icp.local_ratio,
        "has_seeds": bool(seeds),
        "seed_count": len(seeds),
        "trigger_strength": trigger_strength,
        "wants_contacts": icp.wants_contacts,
        "enabled": available,
    }


def plan_search(icp: IcpBody, *, seeds: list[str], available_sources: set[str], target_count: int,
                forced_path: str | None = None) -> SearchPlan:
    cfg = load_yaml("planner_rules")
    feats = _features(icp, seeds, available_sources)
    evaluator = EvalWithCompoundTypes(names=feats)

    weights: dict[str, float] = {}
    rationale: list[str] = []
    if forced_path:
        weights = {forced_path: 1.0}
        rationale.append(f"Path chosen by the user: {PATH_LABELS[forced_path]}")
    else:
        for rule in cfg["rules"]:
            if evaluator.eval(rule["when"]):
                for path, w in rule.get("add", {}).items():
                    weights[path] = weights.get(path, 0.0) + w
                rationale.append(rule["reason"])
                if rule.get("stop"):
                    break

    # Drop paths whose data source is not available (e.g. no place data in live mode).
    for path in list(weights):
        missing = PATH_SOURCES[path] - available_sources
        if missing:
            rationale.append(f"Skipped {path}: source not configured ({', '.join(sorted(missing))})")
            del weights[path]
    if not weights:
        weights = {"B2B_COMPANY": 1.0} if "web_search" in available_sources else {"AUDIENCE_INSIGHT": 1.0}

    total = sum(weights.values())
    shares = sorted(((p, w / total) for p, w in weights.items()), key=lambda t: -t[1])
    shares = [(p, s) for p, s in shares if s >= 0.1]            # tiny shares are not worth a path
    norm = sum(s for _, s in shares)
    paths = [{"path": p, "share": round(s / norm, 2), "label": PATH_LABELS[p]} for p, s in shares]

    # Confidence: how clearly one path wins, lowered for genuinely ambiguous industries.
    top = paths[0]["share"]
    second = paths[1]["share"] if len(paths) > 1 else 0.0
    confidence = 1.0 if forced_path else round(min(1.0, 0.5 + (top - second)), 2)
    question = None
    if not forced_path:
        text = " ".join(icp.industries + [icp.summary]).lower()
        for industry, options in cfg["ambiguous_industries"].items():
            if industry in text:
                confidence = min(confidence, 0.5)
                question = {
                    "text": f"'{industry.title()}' can mean two different kinds of customer. Which do you sell to?",
                    "options": [{"label": options[0], "path": "LOCAL_BUSINESS"},
                                {"label": options[1], "path": "B2B_COMPANY"}],
                }
                rationale.append(f"Industry '{industry}' is ambiguous - asking the user")
                break
    if confidence < cfg["min_confidence"] and question is None:
        question = {"text": "Who should this search target?",
                    "options": [{"label": PATH_LABELS[p["path"]], "path": p["path"]} for p in paths[:2]]}

    plan = SearchPlan(paths=paths, confidence=confidence, rationale=rationale, question=question,
                      decided_by="user" if forced_path else "rules")
    plan.estimate = estimate(plan, target_count)
    return plan


def estimate(plan: SearchPlan, target_count: int) -> dict:
    """
    Rough cost before running. Assumes ~1.4 candidate companies per delivered lead.
    Web searches: ~3 discovery queries per path + 1 people query per company examined.
    AI: offline mode costs nothing; Claude mode ~4k tokens per company (cached prefix).
    """
    candidates = int(target_count * 1.4) + 1
    web_queries = 0
    for p in plan.paths:
        share_candidates = int(candidates * p["share"]) + 1
        if p["path"] in ("B2B_COMPANY", "LOOKALIKE", "SIGNAL_LED"):
            web_queries += 3 + share_candidates
        elif p["path"] in ("LOCAL_BUSINESS", "B2B2C"):
            web_queries += 0                                     # place data + own crawl are free
    settings = get_settings()
    search_usd = web_queries * UNIT_COST_MICRO_USD["web_search"] / 1_000_000
    ai_usd = 0.0
    if settings.llm_mode == "claude":
        ai_usd = candidates * 0.012                              # ~Sonnet fit + Opus judge, cached prefix
    total_paise = round((search_usd + ai_usd) * settings.fx_usd_inr * 100)
    return {
        "target_leads": target_count,
        "candidate_companies": candidates,
        "web_searches": web_queries,
        "ai_mode": settings.llm_mode,
        "cost_paise": total_paise,
        "cost_per_lead_paise": round(total_paise / max(target_count, 1)),
        "quota_leads": target_count,
    }
