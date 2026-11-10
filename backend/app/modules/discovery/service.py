"""
Discovery: run the paths the Search Planner chose and fill the candidate pool.

    B2B_COMPANY     web searches (ICP phrasing x region) -> company websites
    LOOKALIKE       crawl each seed customer -> search for similar businesses
    LOCAL_BUSINESS  place data for the region (clinics, shops) -> their websites
    SIGNAL_LED      web searches for buying-trigger phrases (e.g. hiring)
    B2B2C           LOCAL_BUSINESS run on intermediary categories

Every discovered company is crawled once, merged into the shared knowledge
store, and then gated BEFORE any AI spend:

    company_dead         website does not answer
    out_of_region        located outside every search scope
    excluded_seed        one of the seller's existing customers / exclusions
    duplicate_delivered  already delivered to this workspace (early dedupe)

Dropped candidates keep their reason code, so the Quality Report can show
exactly why each company was not delivered.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from app.core.config import load_yaml
from app.core.db import execute, fetch_one, system_tx, tenant_tx
from app.core.ids import new_id
from app.core.security import lookup_hash
from app.modules.discovery.crawler import CrawlResult, crawl_company, size_band
from app.modules.geo.resolver import company_in_scope
from app.modules.icp.schemas import IcpBody
from app.modules.knowledge import store
from app.modules.knowledge.normalize import registrable_domain
from app.modules.observability.recorder import record_search_event
from app.modules.providers.gateway import ProviderGateway

# Domains that are never a company's own website.
_NOT_COMPANY_SITES = ("linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com",
                      "wikipedia.org", "amazon.in", "amazon.com", "flipkart.com", "justdial.com", "indiamart.com")


@dataclass
class DiscoveryStats:
    found: int = 0
    kept: int = 0
    dropped: dict[str, int] = field(default_factory=dict)
    injections_removed: int = 0
    unsafe_links_blocked: int = 0

    def drop(self, reason: str) -> None:
        self.dropped[reason] = self.dropped.get(reason, 0) + 1


@dataclass
class SearchContext:
    """Everything a discovery run needs to know about the search."""

    workspace_id: uuid.UUID
    search_id: uuid.UUID
    icp: IcpBody
    scopes: list[dict]
    target: int
    include_previously_delivered: bool
    gw: ProviderGateway
    stats: DiscoveryStats = field(default_factory=DiscoveryStats)
    seen_domains: set[str] = field(default_factory=set)


def _place_label(scope: dict) -> str:
    return scope.get("city") or scope["label"].split(",")[0]


def run_discovery(ctx: SearchContext, paths: list[dict]) -> DiscoveryStats:
    pool_cap = ctx.target * load_yaml("pipeline")["candidate_pool_multiplier"]
    for p in paths:
        if _pool_size(ctx) >= pool_cap:
            break
        runner = {"B2B_COMPANY": _run_b2b, "LOOKALIKE": _run_lookalike, "SIGNAL_LED": _run_signals,
                  "LOCAL_BUSINESS": _run_local, "B2B2C": _run_local}.get(p["path"])
        if runner:
            runner(ctx, p["path"], pool_cap)
        record_search_event(workspace_id=ctx.workspace_id, search_id=ctx.search_id, kind="stage",
                            message=f"Discovery path {p['path']} done", data={"pool": _pool_size(ctx)})
    return ctx.stats


# ---------------------------------------------------------------------------
# Path runners
# ---------------------------------------------------------------------------
def _queries_for(icp: IcpBody, place: str) -> list[str]:
    kws = icp.industry_keywords or icp.industries
    phrasings = [
        f"{kws[0]} brands {place}" if kws else f"companies {place}",
        f"{' '.join(kws[1:3])} {place}" if len(kws) > 2 else f"{(kws or ['business'])[0]} {place}",
        f"{(icp.technologies or kws or ['business'])[0]} store {place}",
    ]
    return phrasings[: load_yaml("pipeline")["queries_per_scope"]]


def _search_and_add(ctx: SearchContext, path: str, query: str, pool_cap: int, max_pages: int = 2) -> None:
    for page in range(max_pages):
        if _pool_size(ctx) >= pool_cap:
            return
        results = ctx.gw.web_search(query, count=20, offset=page)
        for r in results:
            domain = registrable_domain(r.url)
            if not domain or domain.endswith(_NOT_COMPANY_SITES) or domain in ctx.seen_domains:
                continue
            name = r.title.split(" - ")[0].split(" | ")[0].strip() or domain
            _add_company(ctx, path, domain, name)
        if len(results) < 20:
            return                                    # no further pages


def _run_b2b(ctx: SearchContext, path: str, pool_cap: int) -> None:
    for scope in ctx.scopes:
        for q in _queries_for(ctx.icp, _place_label(scope)):
            _search_and_add(ctx, path, q, pool_cap)


def _run_signals(ctx: SearchContext, path: str, pool_cap: int) -> None:
    for scope in ctx.scopes:
        for trigger in ctx.icp.triggers[:2]:
            _search_and_add(ctx, path, f"{trigger} {(ctx.icp.industry_keywords or [''])[0]} {_place_label(scope)}",
                            pool_cap, max_pages=1)


def _run_lookalike(ctx: SearchContext, path: str, pool_cap: int) -> None:
    for seed in ctx.icp.exclusions[:5]:
        crawl = crawl_company(ctx.gw, seed)
        if not crawl.alive:
            continue
        # The seed's own description ("a ... pet food brand") is the best lookalike query.
        words = [w for w in crawl.full_text.split()[:30] if w.isalpha() and len(w) > 3][:6]
        for scope in ctx.scopes:
            _search_and_add(ctx, path, f"{' '.join(words)} {_place_label(scope)}", pool_cap, max_pages=1)


def _run_local(ctx: SearchContext, path: str, pool_cap: int) -> None:
    terms = ctx.icp.place_categories or ctx.icp.industry_keywords[:2]
    for scope in ctx.scopes:
        places = ctx.gw.place_search(admin1=scope.get("admin1_code"), city=scope.get("city"), terms=terms)
        for place in places:
            if _pool_size(ctx) >= pool_cap:
                return
            domain = registrable_domain(place.website) if place.website else None
            if domain and domain in ctx.seen_domains:
                continue
            _add_company(ctx, path, domain, place.name, place_phone=place.phone,
                         place_city=place.city, place_admin1=place.admin1)


# ---------------------------------------------------------------------------
# One company: crawl -> knowledge store -> gates -> candidate row
# ---------------------------------------------------------------------------
def _add_company(ctx: SearchContext, path: str, domain: str | None, name: str, *, place_phone: str | None = None,
                 place_city: str | None = None, place_admin1: str | None = None) -> None:
    if domain:
        ctx.seen_domains.add(domain)
    ctx.stats.found += 1
    crawl = crawl_company(ctx.gw, domain) if domain else CrawlResult(domain="", alive=bool(place_phone))
    ctx.stats.injections_removed += crawl.injections_removed
    ctx.stats.unsafe_links_blocked += crawl.unsafe_links_blocked
    if crawl.injections_removed or crawl.unsafe_links_blocked:
        record_search_event(workspace_id=ctx.workspace_id, search_id=ctx.search_id, kind="guardrail",
                            message=f"{domain}: removed {crawl.injections_removed} injection sentence(s), "
                                    f"blocked {crawl.unsafe_links_blocked} unsafe link(s)")

    city = crawl.city or place_city
    admin1 = crawl.admin1 or place_admin1
    with system_tx() as conn:                         # shared knowledge store (global tables)
        company_id = store.upsert_company(
            conn, domain=domain, name=name, city=city, admin1_code=admin1, country_iso2=crawl.country or "IN",
            industry=(ctx.icp.industries or [None])[0] if crawl.alive else None,
            size_band=size_band(crawl.employees), employees=crawl.employees, tech=crawl.tech,
            description=crawl.full_text[:500] or None, alive=crawl.alive, source=path.lower())
        for kind, url, snippet in crawl.evidence:
            store.add_evidence(conn, company_id=company_id, kind=kind, url=url, snippet=snippet)
        phones = crawl.phones or ([place_phone] if place_phone else [])
        for phone in phones[:1]:
            store.upsert_contact(conn, kind="business_phone", value=phone, person_id=None, company_id=company_id,
                                 verify_status="format_valid", confidence=0.7, source=path.lower())
        if crawl.whatsapp:
            store.upsert_contact(conn, kind="whatsapp", value=crawl.whatsapp, person_id=None,
                                 company_id=company_id, verify_status="listed_on_site", confidence=0.7,
                                 source="website")
        company = store.get_company(conn, company_id)

    reason = _gate(ctx, company, domain, crawl)
    with tenant_tx(ctx.workspace_id) as conn:
        execute(conn, """INSERT INTO candidate (id, workspace_id, search_id, company_id, path, dropped_reason)
                         VALUES (:id, :ws, :s, :c, :p, :r) ON CONFLICT (search_id, company_id) DO NOTHING""",
                {"id": new_id(), "ws": ctx.workspace_id, "s": ctx.search_id, "c": company_id, "p": path, "r": reason})
    if reason:
        ctx.stats.drop(reason)
    else:
        ctx.stats.kept += 1


def _gate(ctx: SearchContext, company: dict, domain: str | None, crawl: CrawlResult) -> str | None:
    """Cheap checks before any AI spend. Returns a drop reason code, or None to keep."""
    if not crawl.alive:
        return "company_dead"
    if not company_in_scope(company, ctx.scopes):
        return "out_of_region"
    if domain and domain in {e.lower() for e in ctx.icp.exclusions}:
        return "excluded_seed"
    if domain and not ctx.include_previously_delivered:
        with tenant_tx(ctx.workspace_id) as conn:
            if fetch_one(conn, """SELECT 1 AS x FROM delivered_record WHERE identity_kind = 'business_domain'
                                  AND identity_hmac = :h""", {"h": lookup_hash("business_domain", domain)}):
                return "duplicate_delivered"
    return None


def _pool_size(ctx: SearchContext) -> int:
    return ctx.stats.kept
