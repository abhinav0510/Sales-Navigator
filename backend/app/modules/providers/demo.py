"""
DemoProvider - answers provider operations from the built-in demo dataset.

It behaves like a (slightly noisy) real search engine on purpose:
* web searches match on keywords, so results include brands from OTHER states
  and non-fitting companies - the pipeline must filter them, exactly as it
  would with real Brave results;
* LinkedIn results come back as "Name - Title - Company | LinkedIn" snippets,
  the same shape Brave returns, and are parsed by the same code.
"""
from __future__ import annotations

import re

from app.modules.providers.base import (
    EMAIL_CATCH_ALL, EMAIL_INVALID, EMAIL_VALID, Place, SearchResult,
)
from app.modules.providers.demo_data import (
    DEMO_CATCH_ALL_DOMAINS, DEMO_COMPANIES, DEMO_MAILBOXES,
)

_WORD = re.compile(r"[a-z0-9&]+")
_STATE_NAMES = {"IN-MH": "maharashtra", "IN-GJ": "gujarat", "IN-KA": "karnataka"}
# Words that carry no meaning for matching.
_STOP = {"the", "and", "in", "of", "a", "an", "for", "to", "on", "india", "companies", "brands",
         "brand", "company", "site", "linkedin", "com", "in"}


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP}


class DemoProvider:
    name = "demo"
    #: Sources the Search Planner may use (live mode reports what is configured).
    available_sources = {"web_search", "website", "place", "smtp"}

    # -- web search ---------------------------------------------------------
    def web_search(self, query: str, *, country: str = "IN", count: int = 10,
                   offset: int = 0) -> list[SearchResult]:
        q = query.lower()
        if "site:linkedin.com/in" in q:
            return self._linkedin_search(q, count)
        terms = _words(query)
        scored: list[tuple[int, SearchResult]] = []
        for c in DEMO_COMPANIES.values():
            if c.fit == "clinic":
                continue                                   # clinics come from place search
            home = c.pages.get(f"https://{c.domain}/", "")
            # Like a real engine, a state name in the query also matches businesses located there.
            state = _STATE_NAMES.get(c.admin1, "")
            haystack = _words(f"{c.name} {c.category} {c.city} {state} {home}")
            hits = len(terms & haystack)
            if hits:
                scored.append((hits, SearchResult(
                    title=f"{c.name} - {c.category.title()} | {c.city}",
                    url=f"https://{c.domain}/",
                    snippet=home[:240],
                )))
        scored.sort(key=lambda t: (-t[0], t[1].url))
        start = offset * count                              # Brave-style paging
        return [r for _, r in scored[start:start + count]]

    def _linkedin_search(self, q: str, count: int) -> list[SearchResult]:
        """Return profile snippets for people at the company named in quotes."""
        quoted = re.findall(r'"([^"]+)"', q)
        company_name = quoted[-1] if quoted else ""
        results = []
        for c in DEMO_COMPANIES.values():
            if c.name.lower() != company_name:
                continue
            for p in c.people:
                if not p.linkedin_slug:
                    continue
                results.append(SearchResult(
                    title=f"{p.first} {p.last} - {p.title} - {c.name} | LinkedIn",
                    url=f"https://www.linkedin.com/in/{p.linkedin_slug}",
                    snippet=f"{p.title} at {c.name}. Location: {c.city}, India.",
                ))
        return results[:count]

    # -- page fetch ---------------------------------------------------------
    def fetch_page(self, url: str) -> str | None:
        for c in DEMO_COMPANIES.values():
            if url in c.pages:
                return c.pages[url] if c.alive else None   # dead domain -> no page
        return None

    def site_pages(self, domain: str) -> list[str]:
        """Demo-only helper: the URLs a crawler would discover on a site."""
        for c in DEMO_COMPANIES.values():
            if c.domain == domain:
                return list(c.pages) if c.alive else []
        return []

    # -- place search -------------------------------------------------------
    def place_search(self, *, admin1: str | None, city: str | None, terms: list[str]) -> list[Place]:
        wanted = {t.lower() for t in terms}
        out = []
        for c in DEMO_COMPANIES.values():
            if c.fit != "clinic":
                continue
            if admin1 and c.admin1 != admin1:
                continue
            if city and c.city.lower() != city.lower():
                continue
            if wanted and not (wanted & _words(c.category)):
                continue
            out.append(Place(name=c.name, website=f"https://{c.domain}/", phone=c.phone,
                             city=c.city, admin1=c.admin1, category=c.category))
        return out

    # -- email check --------------------------------------------------------
    def verify_email(self, email: str) -> str:
        domain = email.split("@", 1)[-1].lower()
        if domain in DEMO_CATCH_ALL_DOMAINS:
            return EMAIL_CATCH_ALL
        return EMAIL_VALID if email.lower() in DEMO_MAILBOXES else EMAIL_INVALID
