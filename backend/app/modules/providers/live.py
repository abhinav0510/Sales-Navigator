"""
LiveProvider - real internet sources, assembled from the individual adapters.

    web_search   -> Brave Search API              (needs SN_BRAVE_API_KEY)
    fetch_page   -> WebsiteFetcher (SSRF-guarded)
    place_search -> not available in v1           (Overture / Google Places adapter
                                                    is a planned plug-in; the planner
                                                    skips LOCAL_BUSINESS without it)
    verify_email -> SmtpVerifier if SN_VERIFIER_MODE=smtp, else UNKNOWN
"""
from __future__ import annotations

from app.core.config import get_settings
from app.core.errors import ProviderUnavailable
from app.modules.providers.base import EMAIL_UNKNOWN, Place, SearchResult
from app.modules.providers.brave import BraveSearch
from app.modules.providers.smtp_verifier import SmtpVerifier
from app.modules.providers.website import WebsiteFetcher


class LiveProvider:
    name = "live"

    def __init__(self) -> None:
        settings = get_settings()
        self._brave = BraveSearch(settings.brave_api_key) if settings.brave_api_key else None
        self._web = WebsiteFetcher()
        self._smtp = SmtpVerifier() if settings.verifier_mode == "smtp" else None

    def web_search(self, query: str, *, country: str = "IN", count: int = 10,
                   offset: int = 0) -> list[SearchResult]:
        if self._brave is None:
            raise ProviderUnavailable("Brave key missing", code="no_key")
        return self._brave.search(query, country=country, count=count, offset=offset)

    def fetch_page(self, url: str) -> str | None:
        return self._web.fetch(url)

    def site_pages(self, domain: str) -> list[str]:
        """Pages the crawler tries on a live site (home + common sections)."""
        base = f"https://{domain}"
        return [f"{base}/", f"{base}/about", f"{base}/careers", f"{base}/faq", f"{base}/contact"]

    def place_search(self, *, admin1: str | None, city: str | None, terms: list[str]) -> list[Place]:
        raise ProviderUnavailable("No place-data source configured", code="no_source")

    def verify_email(self, email: str) -> str:
        return self._smtp.verify(email) if self._smtp else EMAIL_UNKNOWN

    @property
    def available_sources(self) -> set[str]:
        sources = {"website"}
        if self._brave:
            sources.add("web_search")
        if self._smtp:
            sources.add("smtp")
        return sources
