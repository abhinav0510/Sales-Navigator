"""
Find the right person at a company and a verified way to reach them.

Order of work for one company:
  1. PEOPLE   B2B paths: web search for LinkedIn profile snippets
              (site:linkedin.com/in "<Company>") -> name + title + slug.
              Local paths: the owner named on the business's own website.
  2. RANK     by persona (persona_rank): founders first in small companies,
              otherwise the ICP's persona order.
  3. EMAIL    for each ranked person: pattern engine candidates -> SMTP check.
  4. SUPPRESSION  any email/phone on the customer's or the global do-not-contact
              list is discarded, and the next person is tried.
  5. CHOOSE   first person with a VALID email; else best catch-all; else best
              person reachable by business phone.

Contact values are written to the shared store encrypted; the lead only keeps
contact_point ids (see leads.service for decryption at read time).
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from app.core.db import fetch_all, system_tx, tenant_tx
from app.modules.compliance.service import is_suppressed
from app.modules.contacts.email_patterns import ROLE_ACCOUNTS, candidates, find_emails, learn_format
from app.modules.contacts.people import classify_title, parse_linkedin_result, persona_rank
from app.modules.icp.schemas import IcpBody
from app.modules.knowledge import store
from app.modules.knowledge.normalize import phone_type, split_name
from app.modules.observability.recorder import record_search_event
from app.modules.providers.base import EMAIL_CATCH_ALL, EMAIL_VALID
from app.modules.providers.gateway import ProviderGateway

_OWNER = re.compile(r"(?:founded by|owner)[^.]*?\b(?:dr\.?\s+)?([A-Z][a-z]+)\s+([A-Z][a-z]+)", re.I)


@dataclass
class Person:
    full_name: str
    title: str
    linkedin_slug: str | None
    rank: int
    source: str


@dataclass
class ContactResult:
    person_id: uuid.UUID | None = None
    full_name: str | None = None
    title: str | None = None
    persona_rank: int | None = None
    linkedin: dict | None = None            # {id, url}
    email: dict | None = None               # {id, status}
    phone: dict | None = None               # {id, type}
    whatsapp: dict | None = None            # {id}
    suppressed_removed: int = 0
    notes: list[str] = field(default_factory=list)


def _people_b2b(gw: ProviderGateway, icp: IcpBody, company: dict) -> list[Person]:
    query = f'site:linkedin.com/in "{company["name"]}"'
    people = []
    for r in gw.web_search(query, count=10):
        prof = parse_linkedin_result(r.title, r.url)
        if not prof or fuzz.token_set_ratio(prof.company, company["name"]) < 85:
            continue                                   # a different company with a similar name
        rank = persona_rank(prof.title, icp.personas, company.get("employees"))
        if rank is not None:
            people.append(Person(prof.full_name, prof.title, prof.linkedin_slug, rank, "linkedin_snippet"))
    return sorted(people, key=lambda p: p.rank)


def _people_local(evidence: list[dict]) -> list[Person]:
    for e in evidence:
        if (m := _OWNER.search(e["snippet"])):
            return [Person(f"{m.group(1)} {m.group(2)}", "Owner", None, 0, "website")]
    return []


def find_contact(*, gw: ProviderGateway, workspace_id: uuid.UUID, search_id: uuid.UUID, icp: IcpBody,
                 company: dict, path: str) -> ContactResult:
    with system_tx() as conn:
        evidence = store.company_evidence(conn, company["id"], limit=20)
        company_contacts = fetch_all(conn, """SELECT id::text AS id, kind FROM contact_point
                                              WHERE company_id = :c AND person_id IS NULL""", {"c": company["id"]})
    local = path in ("LOCAL_BUSINESS", "B2B2C")
    people = _people_local(evidence) if local else _people_b2b(gw, icp, company)
    domain = company.get("domain")
    published = [m for e in evidence for m in find_emails(e["snippet"], domain)] if domain else []
    learned = learn_format(published, [split_name(p.full_name) for p in people])

    result = ContactResult()
    best_catch_all: tuple[Person, str] | None = None
    chosen: tuple[Person, str, str] | None = None        # (person, email, status)

    with tenant_tx(workspace_id) as tconn:               # suppression check sees tenant + global lists
        for person in people:
            first, last = split_name(person.full_name)
            for email in (candidates(first, last, domain, learned=learned) if domain else []):
                status = gw.verify_email(email)
                if status not in (EMAIL_VALID, EMAIL_CATCH_ALL):
                    continue
                if is_suppressed(tconn, "email", email):
                    result.suppressed_removed += 1
                    result.notes.append("suppressed_email_skipped")
                    break                                   # never use it; try the next person
                if status == EMAIL_VALID:
                    chosen = (person, email, status)
                elif best_catch_all is None:
                    best_catch_all = (person, email)
                break
            if chosen:
                break

        # Local businesses: the published generic inbox is the right contact.
        if not chosen and local and domain:
            for email in [e for e in published if e.split("@")[0] in ROLE_ACCOUNTS]:
                if gw.verify_email(email) == EMAIL_VALID and not is_suppressed(tconn, "email", email):
                    owner = people[0] if people else Person("Owner", "Owner", None, 0, "website")
                    chosen = (owner, email, EMAIL_VALID)
                    break

        if not chosen and best_catch_all:
            chosen = (best_catch_all[0], best_catch_all[1], EMAIL_CATCH_ALL)

        phone_row = next((c for c in company_contacts if c["kind"] == "business_phone"), None)
        phone_value = None
        if phone_row:
            with system_tx() as sconn:
                phone_value = store.contact_values(sconn, [phone_row["id"]]).get(phone_row["id"], {}).get("value")
            if phone_value and is_suppressed(tconn, "phone", phone_value):
                result.suppressed_removed += 1
                phone_row = None

    person = chosen[0] if chosen else (people[0] if people else None)
    with system_tx() as conn:
        if person:
            seniority, department = classify_title(person.title)
            result.person_id = store.upsert_person(
                conn, company_id=company["id"], full_name=person.full_name, title=person.title,
                seniority=seniority, department=department, linkedin_slug=person.linkedin_slug,
                confidence=0.9 if person.linkedin_slug else 0.75)
            result.full_name, result.title, result.persona_rank = person.full_name, person.title, person.rank
            if person.linkedin_slug:
                url = f"https://www.linkedin.com/in/{person.linkedin_slug}"
                lid = store.upsert_contact(conn, kind="linkedin", value=url, person_id=result.person_id,
                                           company_id=company["id"], verify_status="matched", confidence=0.85,
                                           source=person.source)
                result.linkedin = {"id": str(lid), "url": url}
        if chosen:
            _, email, status = chosen
            eid = store.upsert_contact(conn, kind="work_email", value=email, person_id=result.person_id,
                                       company_id=company["id"], verify_status=status,
                                       confidence=0.95 if status == EMAIL_VALID else 0.4, source="pattern_smtp")
            result.email = {"id": str(eid), "status": status}
        if phone_row and phone_value:
            result.phone = {"id": phone_row["id"], "type": phone_type(phone_value)}
        wa = next((c for c in company_contacts if c["kind"] == "whatsapp"), None)
        if wa:
            result.whatsapp = {"id": wa["id"]}

    if result.suppressed_removed:
        record_search_event(workspace_id=workspace_id, search_id=search_id, kind="compliance",
                            message=f"{company['name']}: {result.suppressed_removed} suppressed contact(s) skipped")
    return result
