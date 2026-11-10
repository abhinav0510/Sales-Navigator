"""
Brave Search API adapter (live mode).

Endpoint: GET https://api.search.brave.com/res/v1/web/search
Auth:     header X-Subscription-Token: <SN_BRAVE_API_KEY>
Price:    $5 per 1,000 requests (see base.UNIT_COST_MICRO_USD)

Licence note: storing Brave results needs a plan with storage rights. Until
that is confirmed, `STORAGE_ALLOWED = False` keeps Brave responses OUT of the
provider cache (the gateway checks this flag).
"""
from __future__ import annotations

import httpx

from app.core.errors import ProviderUnavailable
from app.modules.providers.base import SearchResult

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
STORAGE_ALLOWED = False


class BraveSearch:
    def __init__(self, api_key: str):
        if not api_key:
            raise ProviderUnavailable("SN_BRAVE_API_KEY is not set", code="no_key")
        self._key = api_key

    def search(self, query: str, *, country: str = "IN", count: int = 10, offset: int = 0) -> list[SearchResult]:
        resp = httpx.get(
            BRAVE_URL,
            params={"q": query, "country": country.lower(), "count": min(count, 20), "offset": min(offset, 9)},
            headers={"X-Subscription-Token": self._key, "Accept": "application/json"},
            timeout=10.0,
        )
        if resp.status_code == 429:
            raise ProviderUnavailable("Brave rate limit", code="rate_limited")
        resp.raise_for_status()
        results = resp.json().get("web", {}).get("results", [])
        return [
            SearchResult(title=r.get("title", ""), url=r.get("url", ""), snippet=r.get("description", ""))
            for r in results
        ]
