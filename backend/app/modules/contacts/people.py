"""
People helpers: LinkedIn snippet parsing, title classification, persona choice.

We never open LinkedIn. A web search like
    site:linkedin.com/in "Head of E-commerce" "Kosh Skincare"
returns result titles shaped like
    "Ananya Kulkarni - Founder & CEO - Kosh Skincare | LinkedIn"
and we read name, title and company from that line only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_LINKEDIN_TITLE = re.compile(
    r"^\s*(?P<name>[^-|–]+?)\s+[-–]\s+(?P<title>.+?)\s+[-–]\s+(?P<company>[^|]+?)\s*\|\s*LinkedIn\s*$")
_SLUG = re.compile(r"linkedin\.com/in/([A-Za-z0-9\-_%]+)")


@dataclass
class ParsedProfile:
    full_name: str
    title: str
    company: str
    linkedin_slug: str | None


def parse_linkedin_result(title: str, url: str) -> ParsedProfile | None:
    m = _LINKEDIN_TITLE.match(title)
    if not m:
        return None
    slug = _SLUG.search(url)
    return ParsedProfile(full_name=m["name"].strip(), title=m["title"].strip(), company=m["company"].strip(),
                         linkedin_slug=slug.group(1) if slug else None)


# Seniority from title keywords, most senior first.
_SENIORITY = [
    ("owner", r"\bowner\b"),
    ("founder", r"\b(co-?)?founder\b"),
    ("c_suite", r"\b(ceo|cto|coo|cfo|cmo|chief)\b"),
    ("head", r"\bhead\b"),
    ("vp", r"\b(vp|vice president)\b"),
    ("director", r"\b(director|md|managing director)\b"),
    ("manager", r"\bmanager\b"),
]
_DEPARTMENTS = [
    ("ecommerce", r"e-?commerce|digital|online"),
    ("operations", r"operations|ops|supply chain|logistics"),
    ("leadership", r"founder|ceo|owner|managing director|md"),
    ("clinical", r"dentist|doctor|dental"),
]


def classify_title(title: str) -> tuple[str | None, str | None]:
    t = title.lower()
    seniority = next((s for s, rx in _SENIORITY if re.search(rx, t)), None)
    department = next((d for d, rx in _DEPARTMENTS if re.search(rx, t)), None)
    return seniority, department


def persona_rank(title: str, personas: list[str], employees: int | None) -> int | None:
    """
    Lower rank = better match. None = not a decision-maker we want.

    Small companies (< 50 people): the founder/owner usually decides, so they rank first.
    Larger companies: follow the ICP persona order (e.g. Head of E-commerce first).
    """
    t = title.lower()
    small = employees is not None and employees < 50
    if small and re.search(r"\b(founder|owner|ceo)\b", t):
        return 0
    for i, persona in enumerate(personas):
        words = [w for w in re.split(r"[^a-z]+", persona.lower()) if len(w) > 2]
        if words and all(w in t for w in words):
            return i + 1
    if re.search(r"\b(founder|owner|ceo)\b", t):
        return len(personas) + 1
    return None
