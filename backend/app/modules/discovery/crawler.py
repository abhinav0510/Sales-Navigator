"""
Company crawler: read a company's public pages and extract facts + evidence.

For each page we:
  1. sanitise the text (remove prompt-injection sentences - counted, not silent)
  2. block links to unsafe hosts (SSRF guard) - counted for the operator
  3. extract facts: city, team size, store platform, published emails, phones, WhatsApp
  4. keep short evidence snippets (what the fit scorer and Quality Judge cite)

The crawler never follows links off the company's own domain.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import urlparse

from app.core.db import fetch_all, system_tx
from app.modules.ai.guardrails import sanitize_evidence
from app.modules.contacts.email_patterns import find_emails
from app.modules.knowledge.normalize import phone_e164
from app.modules.providers.gateway import ProviderGateway
from app.modules.providers.website import is_safe_url

_URL = re.compile(r"https?://[^\s)\"'<>]+")
_TEAM = re.compile(r"team of (\d{1,5})", re.I)
_PHONE = re.compile(r"(?<![\w/])(\+?\d[\d\s\-]{9,14}\d)")
_WHATSAPP = re.compile(r"wa\.me/(\d{10,15})|api\.whatsapp\.com/send\?phone=(\d{10,15})")
_PLATFORMS = ["Shopify", "WooCommerce", "Amazon", "Flipkart", "Myntra"]


@dataclass
class CrawlResult:
    domain: str
    alive: bool
    city: str | None = None
    admin1: str | None = None
    country: str | None = "IN"
    employees: int | None = None
    tech: list[str] = field(default_factory=list)
    evidence: list[tuple[str, str, str]] = field(default_factory=list)   # (kind, url, snippet)
    published_emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    whatsapp: str | None = None
    pages_read: int = 0
    injections_removed: int = 0
    unsafe_links_blocked: int = 0
    full_text: str = ""


@lru_cache(maxsize=1)
def _known_cities() -> list[tuple[str, str, str, str]]:
    """(name-or-alias lower, admin1, country, canonical name) for every city in the place table."""
    with system_tx() as conn:
        rows = fetch_all(conn, "SELECT name, ascii_name, aliases, admin1_code, country_iso2 FROM geo_place WHERE kind='city'")
    out = []
    for r in rows:
        for n in {r["ascii_name"], *r["aliases"]}:
            out.append((n, r["admin1_code"], r["country_iso2"], r["name"]))
    # Longest names first so "navi mumbai" wins over "mumbai".
    return sorted(out, key=lambda t: -len(t[0]))


def _find_city(text: str) -> tuple[str | None, str | None, str | None]:
    low = text.lower()
    for name, admin1, iso, canonical in _known_cities():
        if re.search(rf"\b{re.escape(name)}\b", low):
            return canonical, admin1, iso
    return None, None, None


def size_band(employees: int | None) -> str | None:
    if employees is None:
        return None
    if employees < 10:
        return "micro"
    if employees < 100:
        return "smb"
    if employees < 1000:
        return "mid"
    return "enterprise"


def _page_kind(url: str) -> str:
    path = urlparse(url).path.lower()
    if "career" in path or "jobs" in path:
        return "job"
    return "website"


def crawl_company(gw: ProviderGateway, domain: str) -> CrawlResult:
    result = CrawlResult(domain=domain, alive=False)
    texts: list[str] = []
    for url in gw.site_pages(domain):
        try:
            raw = gw.fetch_page(url)
        except Exception:
            raw = None
        if not raw:
            continue
        result.pages_read += 1

        # Guardrail 1: links to unsafe hosts are never followed (and are counted).
        for link in _URL.findall(raw):
            if not is_safe_url(link, resolve=False):
                result.unsafe_links_blocked += 1
        # Guardrail 2: instruction-like sentences are stripped from evidence.
        clean, removed = sanitize_evidence(_URL.sub(" ", raw))
        result.injections_removed += removed

        texts.append(clean)
        result.published_emails += find_emails(raw, domain)
        for m in _PHONE.findall(raw):
            if (e164 := phone_e164(m)) and e164 not in result.phones:
                result.phones.append(e164)
        if (wa := _WHATSAPP.search(raw)) and not result.whatsapp:
            result.whatsapp = phone_e164("+" + (wa.group(1) or wa.group(2)))
        if clean:
            result.evidence.append((_page_kind(url), url, clean[:300]))

    # Home page must answer for the company to count as alive.
    result.alive = result.pages_read > 0
    result.full_text = " ".join(texts)
    if result.alive:
        result.city, result.admin1, result.country = _find_city(texts[0] if texts else "")
        if (m := _TEAM.search(result.full_text)):
            result.employees = int(m.group(1))
        result.tech = [p for p in _PLATFORMS if p.lower() in result.full_text.lower()]
        for p in result.tech:
            if p in ("Shopify", "WooCommerce"):
                result.evidence.append(("tech", f"https://{domain}/", f"Online store runs on {p}."))
    return result
