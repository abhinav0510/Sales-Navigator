"""
Region resolver: turns whatever the user typed into exact search scopes.

    "Maharashtra"                 -> state  IN-MH
    "Pune, Mumbai and Nagpur"     -> three city scopes
    "within 25 km of Pune"        -> radius scope (centre + km)
    "Bombay"                      -> Mumbai (alias)
    "Aurangabad"                  -> AMBIGUOUS (Maharashtra or Bihar) -> user picks
    "global"                      -> ranked market list
    "Baner near Balewadi"         -> UNRESOLVED -> did-you-mean + fallbacks

No-dead-end rule (LEAD_FLOW region step): the caller passes `attempt`.
After 2 failed attempts the response sets `forced_choice=True`; the UI then
removes the free-text box and offers only options that always resolve
(a suggestion, a state/city list, or a map pin + radius). Every unresolved
string is logged to `geo_unresolved` for the operator.
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache

from sqlalchemy import Connection
from unidecode import unidecode

from app.core.db import execute, fetch_all, fetch_one, system_tx
from app.modules.geo.places_seed import GLOBAL_MARKET_ORDER, GROUPS

MAX_ATTEMPTS_BEFORE_FORCED_CHOICE = 2
_SPLIT = re.compile(r"\s*(?:,|;|\+(?!\s*\d)|\band\b|&)\s*", re.I)
_RADIUS_PREFIX = re.compile(
    r"^(?:within\s+)?(\d+(?:\.\d+)?)\s*(km|kms|kilometers|kilometres|mi|miles)\s*(?:of|around|from|near)?\s+(.+)$", re.I)
_RADIUS_SUFFIX = re.compile(r"^(.+?)\s*\+?\s*(\d+(?:\.\d+)?)\s*(km|kms|mi|miles)$", re.I)
_NOISE = re.compile(r"\b(city|district|state of|region|area)\b", re.I)


@dataclass
class GeoScope:
    level: str                     # global | country | state | city | radius
    label: str
    country_iso2: str | None = None
    admin1_code: str | None = None
    city: str | None = None
    lat: float | None = None
    lon: float | None = None
    radius_km: float | None = None


@dataclass
class Resolution:
    status: str                                       # resolved | ambiguous | unresolved
    scopes: list[GeoScope] = field(default_factory=list)
    ambiguities: list[dict] = field(default_factory=list)
    unresolved: list[dict] = field(default_factory=list)
    forced_choice: bool = False
    allow_map_pin: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


def normalize(text: str) -> str:
    """Lower-case, strip accents and filler words ('city', 'district', ...)."""
    text = unidecode(text).lower().strip()
    text = _NOISE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip(" .")


def _row_to_scope(row: dict, radius_km: float | None = None) -> GeoScope:
    kind = row["kind"]
    # A district is searched like a city; any place + km becomes a radius search.
    level = "radius" if radius_km else ("city" if kind == "district" else kind)
    label = row["name"] if kind == "country" else f"{row['name']}, {row['parent_name'] or row['country_iso2']}"
    if radius_km:
        label = f"Within {radius_km:g} km of {row['name']}"
    return GeoScope(level=level, label=label, country_iso2=row["country_iso2"],
                    admin1_code=row["admin1_code"], city=row["name"] if kind == "city" else None,
                    lat=row["lat"], lon=row["lon"], radius_km=radius_km)


def _exact(conn: Connection, name: str) -> list[dict]:
    return fetch_all(conn, """
        SELECT * FROM geo_place
        WHERE ascii_name = :n OR :n = ANY(aliases)
        ORDER BY CASE kind WHEN 'country' THEN 0 WHEN 'state' THEN 1 ELSE 2 END, population DESC""",
        {"n": name})


def _similar(conn: Connection, name: str, limit: int = 5) -> list[dict]:
    return fetch_all(conn, """
        SELECT *, similarity(ascii_name, :n) AS sim FROM geo_place
        WHERE similarity(ascii_name, :n) > 0.3
        ORDER BY sim DESC, population DESC LIMIT :lim""", {"n": name, "lim": limit})


def _contained_places(conn: Connection, name: str) -> list[dict]:
    """Known places mentioned inside an unknown phrase ('Baner, near Pune' -> Pune)."""
    tokens = [t for t in re.split(r"[^a-z]+", name) if len(t) >= 4]
    found: list[dict] = []
    for t in tokens:
        found.extend(_exact(conn, t))
    return found


def _log_unresolved(conn: Connection, text: str) -> None:
    execute(conn, """INSERT INTO geo_unresolved (text) VALUES (:t)
                     ON CONFLICT (text) DO UPDATE SET count = geo_unresolved.count + 1, last_at = now()""",
            {"t": text[:200]})


def resolve(text: str, *, attempt: int = 1) -> Resolution:
    """Resolve a free-text region. `attempt` is how many times the user has tried (1-based)."""
    res = Resolution(status="resolved")
    with system_tx() as conn:
        # Names that contain "and" / "&" (e.g. "Jammu and Kashmir") must not be split,
        # so try the whole text first and split into a list only if that fails.
        whole = normalize(text)
        raw_items = [text.strip()] if _exact(conn, whole) else [p for p in _SPLIT.split(text.strip()) if p]
        for raw in raw_items:
            item = normalize(raw)
            if not item:
                continue
            if item in ("global", "worldwide", "anywhere", "all countries"):
                res.scopes.extend(_global_markets(conn))
                continue
            if item in GROUPS:
                for member in GROUPS[item]:
                    rows = _exact(conn, normalize(member))
                    if rows:
                        res.scopes.append(_row_to_scope(rows[0]))
                continue

            radius_km = None
            m = _RADIUS_PREFIX.match(item) or None
            if m:
                radius_km, unit, item = float(m.group(1)), m.group(2), normalize(m.group(3))
            else:
                m = _RADIUS_SUFFIX.match(item)
                if m:
                    item, radius_km, unit = normalize(m.group(1)), float(m.group(2)), m.group(3)
            if radius_km and unit.lower().startswith("mi"):
                radius_km = round(radius_km * 1.609, 1)

            rows = _exact(conn, item)
            # Same name, different places (e.g. Aurangabad MH / BR) -> ask the user.
            cities = [r for r in rows if r["kind"] == "city"]
            if len(cities) > 1 and not any(r["kind"] in ("country", "state") for r in rows):
                res.ambiguities.append({"text": raw, "options": [asdict(_row_to_scope(r, radius_km)) for r in cities]})
                continue
            if rows:
                res.scopes.append(_row_to_scope(rows[0], radius_km))
                continue

            _log_unresolved(conn, raw)
            res.unresolved.append({
                "text": raw,
                "did_you_mean": [asdict(_row_to_scope(r, radius_km)) for r in _similar(conn, item)],
                "parent": [asdict(_row_to_scope(r)) for r in _contained_places(conn, item)],
            })

    if res.unresolved:
        res.status = "unresolved"
        res.forced_choice = attempt >= MAX_ATTEMPTS_BEFORE_FORCED_CHOICE
    elif res.ambiguities:
        res.status = "ambiguous"
    return res


def _global_markets(conn: Connection) -> list[GeoScope]:
    rows = fetch_all(conn, "SELECT * FROM geo_place WHERE kind = 'country'")
    by_iso = {r["country_iso2"]: r for r in rows}
    return [_row_to_scope(by_iso[iso]) for iso in GLOBAL_MARKET_ORDER if iso in by_iso]


# ---------------------------------------------------------------------------
# Scope membership - used by discovery and the Quality Judge.
# ---------------------------------------------------------------------------
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@lru_cache(maxsize=2048)
def city_coords(city: str, admin1: str | None) -> tuple[float, float] | None:
    with system_tx() as conn:
        row = fetch_one(conn, """SELECT lat, lon FROM geo_place WHERE kind='city' AND ascii_name = :n
                                 AND (admin1_code = :a OR :a IS NULL) ORDER BY population DESC LIMIT 1""",
                        {"n": normalize(city), "a": admin1})
    return (row["lat"], row["lon"]) if row else None


def company_in_scope(company: dict, scopes: list[dict]) -> bool:
    """True if the company's location falls inside ANY of the search scopes."""
    for s in scopes:
        level = s["level"]
        if level == "global":
            return True
        if level == "country" and company.get("country_iso2") == s["country_iso2"]:
            return True
        if level == "state" and company.get("admin1_code") == s["admin1_code"]:
            return True
        if level == "city" and (company.get("city") or "").lower() == (s["city"] or "").lower() \
                and company.get("admin1_code") == s["admin1_code"]:
            return True
        if level == "radius" and company.get("city"):
            coords = city_coords(company["city"], company.get("admin1_code"))
            if coords and haversine_km(coords[0], coords[1], s["lat"], s["lon"]) <= s["radius_km"]:
                return True
    return False
