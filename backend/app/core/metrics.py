"""
Prometheus metrics. Exposed at GET /metrics (scraped by Prometheus/Grafana).

Naming: `sn_<area>_<what>_<unit>`. Labels are low-cardinality only
(never put workspace ids or emails in labels - that explodes storage).
"""
from __future__ import annotations

from prometheus_client import Counter, Histogram

HTTP_REQUESTS = Counter(
    "sn_http_requests_total", "HTTP requests", ["route", "method", "status"]
)
HTTP_LATENCY = Histogram(
    "sn_http_latency_seconds", "HTTP request latency", ["route"]
)
PROVIDER_CALLS = Counter(
    "sn_provider_calls_total", "Calls through the provider gateway",
    ["provider", "op", "status", "cache"],
)
PROVIDER_COST = Counter(
    "sn_provider_cost_micro_usd_total", "Provider spend in micro-USD", ["provider"]
)
LLM_TOKENS = Counter(
    "sn_llm_tokens_total", "LLM tokens", ["task", "model", "kind"]  # kind = in|cached|out
)
LLM_CALLS = Counter(
    "sn_llm_calls_total", "LLM calls", ["task", "model", "status"]
)
QUALITY_DECISIONS = Counter(
    "sn_quality_decisions_total", "Quality Judge decisions", ["decision", "path"]
)
SEARCH_OUTCOMES = Counter(
    "sn_search_outcome_total", "Finished searches", ["status"]
)
DELIVERED_LEADS = Counter(
    "sn_delivered_leads_total", "Leads delivered to customers", ["plan"]
)
