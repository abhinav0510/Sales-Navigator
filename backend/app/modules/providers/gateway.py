"""
Provider gateway - the ONLY door from the pipeline to external data.

Every call passes through the same checks, in this order:

    1. cache        - return a stored response if fresh (free, recorded as a cache hit)
    2. breaker      - refuse fast if this provider has been failing (circuit open)
    3. budget       - refuse if the call would push the search over its budget
    4. call + retry - run it; retry transient failures with backoff
    5. record       - write a provider_call row + Prometheus metrics, update spend
    6. cache store  - keep the response if the source's licence allows storage

Because all of this lives here, pipeline stages stay simple: they call
`gateway.web_search(...)` and never think about money, retries or caching.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from app.core.config import get_settings
from app.core.db import as_json, execute, fetch_one, system_tx
from app.core.errors import BudgetExhausted, ProviderUnavailable
from app.core.metrics import PROVIDER_CALLS, PROVIDER_COST
from app.modules.observability.recorder import record_provider_call
from app.modules.providers.base import UNIT_COST_MICRO_USD, Place, SearchResult

# How long each kind of response stays fresh in the cache.
CACHE_TTL = {
    "web_search": timedelta(days=14),
    "fetch_page": timedelta(days=7),
    "place_search": timedelta(days=30),
    "verify_email": timedelta(days=14),
}


# ---------------------------------------------------------------------------
# Budget tracking
# ---------------------------------------------------------------------------
@dataclass
class BudgetTracker:
    """Tracks spend for one search against its budget (both in paise)."""

    budget_paise: int
    spent_paise: float = 0.0

    def cost_paise(self, micro_usd: int) -> float:
        return micro_usd / 1_000_000 * get_settings().fx_usd_inr * 100

    def check(self, micro_usd: int) -> None:
        if self.spent_paise + self.cost_paise(micro_usd) > self.budget_paise:
            raise BudgetExhausted("Search budget reached")

    def charge(self, micro_usd: int) -> None:
        self.spent_paise += self.cost_paise(micro_usd)


# ---------------------------------------------------------------------------
# Circuit breaker (per provider, per process)
# ---------------------------------------------------------------------------
@dataclass
class CircuitBreaker:
    """Opens after `threshold` consecutive failures; retries after `cooldown_s`."""

    threshold: int = 5
    cooldown_s: float = 30.0
    failures: int = 0
    opened_at: float | None = None

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        if time.monotonic() - self.opened_at >= self.cooldown_s:
            self.opened_at = None          # half-open: let the next call probe
            self.failures = 0
            return True
        return False

    def record(self, ok: bool) -> None:
        if ok:
            self.failures = 0
            return
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = time.monotonic()


_BREAKERS: dict[str, CircuitBreaker] = {}


# ---------------------------------------------------------------------------
# The gateway
# ---------------------------------------------------------------------------
@dataclass
class ProviderGateway:
    provider: Any                                # DemoProvider | LiveProvider
    workspace_id: uuid.UUID | None = None
    search_id: uuid.UUID | None = None
    budget: BudgetTracker | None = None
    cacheable_ops: set[str] = field(default_factory=lambda: {"fetch_page", "place_search", "verify_email"})

    # --- public operations ---------------------------------------------------
    def web_search(self, query: str, *, country: str = "IN", count: int = 10, offset: int = 0) -> list[SearchResult]:
        raw = self._call("web_search", {"q": query, "country": country, "count": count, "offset": offset},
                         lambda: [r.__dict__ for r in self.provider.web_search(
                             query, country=country, count=count, offset=offset)])
        return [SearchResult(**r) for r in raw]

    def fetch_page(self, url: str) -> str | None:
        return self._call("fetch_page", {"url": url}, lambda: self.provider.fetch_page(url))

    def place_search(self, *, admin1: str | None, city: str | None, terms: list[str]) -> list[Place]:
        params = {"admin1": admin1, "city": city, "terms": sorted(terms)}
        raw = self._call("place_search", params, lambda: [
            p.__dict__ for p in self.provider.place_search(admin1=admin1, city=city, terms=terms)])
        return [Place(**p) for p in raw]

    def verify_email(self, email: str) -> str:
        return self._call("verify_email", {"email": email.lower()}, lambda: self.provider.verify_email(email))

    def site_pages(self, domain: str) -> list[str]:
        return self.provider.site_pages(domain)

    @property
    def available_sources(self) -> set[str]:
        return set(self.provider.available_sources)

    # --- the pipeline every call goes through --------------------------------
    def _call(self, op: str, params: dict, fn: Callable[[], Any]) -> Any:
        name = self.provider.name
        key = hashlib.sha256(f"{name}|{op}|{json.dumps(params, sort_keys=True)}".encode()).hexdigest()

        # 1. cache
        if op in self.cacheable_ops:
            hit = self._cache_get(key)
            if hit is not None:
                self._record(op, cost=0, cache_hit=True, status="ok", latency_ms=0)
                return hit["v"]

        # 2. breaker
        breaker = _BREAKERS.setdefault(name, CircuitBreaker())
        if not breaker.allow():
            self._record(op, cost=0, cache_hit=False, status="circuit_open", latency_ms=0)
            raise ProviderUnavailable(f"{name} circuit open", code="circuit_open")

        # 3. budget
        cost = UNIT_COST_MICRO_USD.get(op, 0)
        if self.budget is not None:
            self.budget.check(cost)

        # 4. call with retry (transient failures only)
        started = time.monotonic()
        result, status = None, "ok"
        for attempt in range(3):
            try:
                result = fn()
                breaker.record(ok=True)
                break
            except ProviderUnavailable:
                breaker.record(ok=False)
                self._record(op, cost=0, cache_hit=False, status="unavailable",
                             latency_ms=int((time.monotonic() - started) * 1000))
                raise
            except Exception:
                breaker.record(ok=False)
                status = "error"
                if attempt == 2:
                    self._record(op, cost=0, cache_hit=False, status=status,
                                 latency_ms=int((time.monotonic() - started) * 1000))
                    raise
                time.sleep(0.5 * (2 ** attempt))

        # 5. record + charge
        if self.budget is not None:
            self.budget.charge(cost)
        self._record(op, cost=cost, cache_hit=False, status="ok",
                     latency_ms=int((time.monotonic() - started) * 1000))

        # 6. cache store
        if op in self.cacheable_ops and result is not None:
            self._cache_put(key, op, {"v": result}, CACHE_TTL[op])
        return result

    def _record(self, op: str, *, cost: int, cache_hit: bool, status: str, latency_ms: int) -> None:
        name = self.provider.name
        PROVIDER_CALLS.labels(name, op, status, "hit" if cache_hit else "miss").inc()
        if cost:
            PROVIDER_COST.labels(name).inc(cost)
        record_provider_call(workspace_id=self.workspace_id, search_id=self.search_id, provider=name,
                             op=op, cost_micro_usd=cost, cache_hit=cache_hit, status=status,
                             latency_ms=latency_ms)

    @staticmethod
    def _cache_get(key: str) -> dict | None:
        with system_tx() as conn:
            row = fetch_one(conn, "SELECT body FROM provider_cache WHERE key = :k AND expires_at > now()",
                            {"k": key})
        return row["body"] if row else None

    @staticmethod
    def _cache_put(key: str, op: str, body: dict, ttl: timedelta) -> None:
        with system_tx() as conn:
            execute(conn, """INSERT INTO provider_cache (key, provider, op, body, expires_at)
                             VALUES (:k, 'gateway', :op, CAST(:b AS jsonb), :exp)
                             ON CONFLICT (key) DO UPDATE SET body = EXCLUDED.body, expires_at = EXCLUDED.expires_at""",
                    {"k": key, "op": op, "b": as_json(body), "exp": datetime.now(UTC) + ttl})


def build_gateway(*, workspace_id: uuid.UUID | None = None, search_id: uuid.UUID | None = None,
                  budget: BudgetTracker | None = None) -> ProviderGateway:
    """Create a gateway for the configured data mode (demo or live)."""
    if get_settings().data_mode == "live":
        from app.modules.providers.live import LiveProvider
        from app.modules.providers.brave import STORAGE_ALLOWED

        gw = ProviderGateway(LiveProvider(), workspace_id, search_id, budget)
        if STORAGE_ALLOWED:
            gw.cacheable_ops.add("web_search")
        return gw
    from app.modules.providers.demo import DemoProvider

    gw = ProviderGateway(DemoProvider(), workspace_id, search_id, budget)
    gw.cacheable_ops.add("web_search")          # no licence restriction on demo data
    return gw
