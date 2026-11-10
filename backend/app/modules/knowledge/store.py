"""
Shared knowledge store - public business facts reused by every tenant (HLD 7.2).

Write path (pipeline):   upsert_company / upsert_person / upsert_contact / add_evidence
Read path (pipeline/API): get_company / company_evidence / contact_values

Entity resolution (is this the same company we already know?):
    1. same registrable domain                      -> same company
    2. same normalised name AND same city           -> same company
    3. otherwise                                    -> new company
People: same LinkedIn slug, else same (company, normalised name).

Contact values are stored encrypted (AES-GCM) with a keyed hash for lookup,
so the database never holds readable emails or phone numbers.
"""
from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import Connection

from app.core.db import execute, fetch_all, fetch_one
from app.core.ids import new_id
from app.core.security import decrypt, encrypt, lookup_hash
from app.modules.knowledge.normalize import company_name_key, person_name_key


def upsert_company(conn: Connection, *, domain: str | None, name: str, city: str | None,
                   admin1_code: str | None, country_iso2: str | None = "IN", industry: str | None = None,
                   size_band: str | None = None, employees: int | None = None, tech: list[str] | None = None,
                   description: str | None = None, alive: bool = True, source: str = "web") -> uuid.UUID:
    name_key = company_name_key(name)
    existing = None
    if domain:
        existing = fetch_one(conn, "SELECT id FROM company WHERE domain = :d", {"d": domain})
    if existing is None and city:
        existing = fetch_one(conn, "SELECT id FROM company WHERE name_norm = :n AND lower(city) = lower(:c)",
                             {"n": name_key, "c": city})
    if existing:
        # Merge: keep known values, fill gaps, add the new source, refresh liveness.
        execute(conn, """UPDATE company SET
                           domain = COALESCE(domain, :d), city = COALESCE(city, :c),
                           admin1_code = COALESCE(admin1_code, :a), industry = COALESCE(:ind, industry),
                           size_band = COALESCE(:sb, size_band), employees = COALESCE(:emp, employees),
                           tech = CASE WHEN cardinality(CAST(:tech AS text[])) > 0 THEN CAST(:tech AS text[]) ELSE tech END,
                           description = COALESCE(:desc, description), alive = :alive,
                           sources = (SELECT array_agg(DISTINCT s) FROM unnest(sources || CAST(:src AS text[])) s),
                           confidence = LEAST(1.0, confidence + 0.1), updated_at = now()
                         WHERE id = :id""",
                {"id": existing["id"], "d": domain, "c": city, "a": admin1_code, "ind": industry,
                 "sb": size_band, "emp": employees, "tech": tech or [], "desc": description,
                 "alive": alive, "src": [source]})
        return existing["id"]
    cid = new_id()
    execute(conn, """INSERT INTO company (id, domain, name, name_norm, industry, size_band, employees, city,
                                          admin1_code, country_iso2, tech, description, alive, sources, confidence)
                     VALUES (:id, :d, :n, :nk, :ind, :sb, :emp, :c, :a, :iso, :tech, :desc, :alive, :src, 0.6)""",
            {"id": cid, "d": domain, "n": name, "nk": name_key, "ind": industry, "sb": size_band, "emp": employees,
             "c": city, "a": admin1_code, "iso": country_iso2, "tech": tech or [], "desc": description,
             "alive": alive, "src": [source]})
    return cid


def upsert_person(conn: Connection, *, company_id: uuid.UUID, full_name: str, title: str | None,
                  seniority: str | None, department: str | None, linkedin_slug: str | None,
                  confidence: float) -> uuid.UUID:
    key = person_name_key(full_name)
    row = None
    if linkedin_slug:
        row = fetch_one(conn, "SELECT id FROM person WHERE linkedin_slug = :s", {"s": linkedin_slug})
    if row is None:
        row = fetch_one(conn, "SELECT id FROM person WHERE company_id = :c AND name_norm = :n",
                        {"c": company_id, "n": key})
    if row:
        execute(conn, """UPDATE person SET title = COALESCE(:t, title), seniority = COALESCE(:s, seniority),
                           department = COALESCE(:d, department), linkedin_slug = COALESCE(linkedin_slug, :l),
                           company_id = :c, confidence = GREATEST(confidence, :conf) WHERE id = :id""",
                {"id": row["id"], "t": title, "s": seniority, "d": department, "l": linkedin_slug,
                 "c": company_id, "conf": confidence})
        return row["id"]
    pid = new_id()
    execute(conn, """INSERT INTO person (id, company_id, full_name, name_norm, title, seniority, department,
                                         linkedin_slug, confidence)
                     VALUES (:id, :c, :n, :nk, :t, :s, :d, :l, :conf)""",
            {"id": pid, "c": company_id, "n": full_name, "nk": key, "t": title, "s": seniority,
             "d": department, "l": linkedin_slug, "conf": confidence})
    return pid


def upsert_contact(conn: Connection, *, kind: str, value: str, person_id: uuid.UUID | None,
                   company_id: uuid.UUID | None, verify_status: str, confidence: float, source: str) -> uuid.UUID:
    """Store (or refresh) a contact value. Identical values are one row (dedupe by keyed hash)."""
    h = lookup_hash(kind, value)
    row = fetch_one(conn, "SELECT id FROM contact_point WHERE kind = :k AND value_hmac = :h", {"k": kind, "h": h})
    if row:
        execute(conn, """UPDATE contact_point SET verify_status = :vs, confidence = :conf, verified_at = now(),
                           sources = (SELECT array_agg(DISTINCT s) FROM unnest(sources || CAST(:src AS text[])) s)
                         WHERE id = :id""",
                {"id": row["id"], "vs": verify_status, "conf": confidence, "src": [source]})
        return row["id"]
    cid = new_id()
    execute(conn, """INSERT INTO contact_point (id, person_id, company_id, kind, value_enc, value_hmac,
                                                verify_status, confidence, sources, verified_at)
                     VALUES (:id, :p, :c, :k, :enc, :h, :vs, :conf, :src, now())""",
            {"id": cid, "p": person_id, "c": company_id, "k": kind, "enc": encrypt(value), "h": h,
             "vs": verify_status, "conf": confidence, "src": [source]})
    return cid


def add_evidence(conn: Connection, *, company_id: uuid.UUID, kind: str, url: str | None, snippet: str) -> str:
    """Store one piece of evidence (deduplicated by content). Returns its id as a string."""
    content_hash = hashlib.sha256(f"{kind}|{snippet}".encode()).hexdigest()
    row = fetch_one(conn, "SELECT id FROM evidence WHERE company_id = :c AND content_hash = :h",
                    {"c": company_id, "h": content_hash})
    if row:
        return str(row["id"])
    eid = new_id()
    execute(conn, """INSERT INTO evidence (id, company_id, kind, url, snippet, content_hash)
                     VALUES (:id, :c, :k, :u, :s, :h)""",
            {"id": eid, "c": company_id, "k": kind, "u": url, "s": snippet[:600], "h": content_hash})
    return str(eid)


def get_company(conn: Connection, company_id: uuid.UUID) -> dict | None:
    return fetch_one(conn, "SELECT * FROM company WHERE id = :id", {"id": company_id})


def company_evidence(conn: Connection, company_id: uuid.UUID, limit: int = 12) -> list[dict]:
    return fetch_all(conn, """SELECT id::text AS id, kind, url, snippet FROM evidence
                              WHERE company_id = :c ORDER BY observed_at DESC LIMIT :l""",
                     {"c": company_id, "l": limit})


def contact_values(conn: Connection, ids: list[str]) -> dict[str, dict]:
    """Decrypt contact points by id -> {id: {kind, value, verify_status, ...}} (for the API only)."""
    if not ids:
        return {}
    rows = fetch_all(conn, """SELECT id::text AS id, kind, value_enc, verify_status, confidence, sources, verified_at
                              FROM contact_point WHERE id = ANY(CAST(:ids AS uuid[]))""", {"ids": ids})
    out = {}
    for r in rows:
        r["value"] = decrypt(r.pop("value_enc"))
        out[r["id"]] = r
    return out
