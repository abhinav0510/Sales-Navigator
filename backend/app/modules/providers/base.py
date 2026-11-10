"""
Provider interface - the contract every external data source implements.

The pipeline never talks to Brave, a website or a mail server directly. It asks
the **gateway** (gateway.py) for one of four operations, and the gateway routes
the call to whichever provider is configured:

    web_search    -> find company websites, LinkedIn profile snippets, news
    fetch_page    -> read a public web page (the crawler)
    place_search  -> list local businesses in an area (clinics, shops, ...)
    verify_email  -> does this mailbox exist? (no email is ever sent)

Swapping a vendor = writing one class with these methods. Nothing else changes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


@dataclass(frozen=True)
class Place:
    name: str
    website: str | None
    phone: str | None
    city: str
    admin1: str
    category: str


# Email verification outcomes (see verification in LLD 6.9).
EMAIL_VALID = "valid"
EMAIL_INVALID = "invalid"
EMAIL_CATCH_ALL = "catch_all"
EMAIL_UNKNOWN = "unknown"


class DataProvider(Protocol):
    """One implementation per data mode (demo.py, live.py)."""

    name: str

    def web_search(self, query: str, *, country: str = "IN", count: int = 10,
                   offset: int = 0) -> list[SearchResult]: ...

    def fetch_page(self, url: str) -> str | None: ...

    def place_search(self, *, admin1: str | None, city: str | None, terms: list[str]) -> list[Place]: ...

    def verify_email(self, email: str) -> str: ...


# Unit cost of each operation, in micro-USD (1 USD = 1,000,000).
# Brave Search API: $5 per 1,000 requests (checked 2026-11-10) = 5,000 micro-USD.
# Page fetches, place data (open datasets) and our own SMTP checks cost nothing per call.
UNIT_COST_MICRO_USD: dict[str, int] = {
    "web_search": 5_000,
    "fetch_page": 0,
    "place_search": 0,
    "verify_email": 0,
}
