"""
Email pattern engine - work out a person's work email without a paid provider.

1. LEARN the domain's format from any address published on the company's own
   pages that belongs to a person we know (e.g. "write to sneha.joshi@brand.in"
   + we know Sneha Joshi works there -> the format is "{first}.{last}").
2. Otherwise fall back to the country's most common formats (config/pipeline.yaml).
3. Generate candidates in order; each is checked by the SMTP verifier.
   The first VALID one wins. A catch-all domain keeps the most likely candidate,
   clearly labelled catch_all (never presented as verified).
"""
from __future__ import annotations

import re

from unidecode import unidecode

from app.core.config import load_yaml

_EMAIL = re.compile(r"\b([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
FORMATS = ["{first}.{last}", "{first}", "{f}{last}", "{first}{last}", "{first}_{last}", "{f}.{last}",
           "{first}{l}", "{last}.{first}", "{last}{f}", "{first}-{last}"]
ROLE_ACCOUNTS = {"info", "sales", "support", "hello", "contact", "admin", "press", "media", "careers", "hr"}


def _clean(s: str) -> str:
    return re.sub(r"[^a-z]", "", unidecode(s).lower())


def render(fmt: str, first: str, last: str) -> str | None:
    first, last = _clean(first), _clean(last)
    if not first or ("{last}" in fmt or "{l}" in fmt) and not last:
        return None                       # single-name person: only first-name formats apply
    return fmt.format(first=first, last=last, f=first[:1], l=last[:1])


def find_emails(text: str, domain: str) -> list[str]:
    """Email addresses on `domain` that appear in a page's text."""
    return [f"{u}@{d}".lower() for u, d in _EMAIL.findall(text) if d.lower() == domain]


def learn_format(published: list[str], people: list[tuple[str, str]]) -> str | None:
    """Return the format that maps a known person to a published address, if any."""
    for email in published:
        local = email.split("@", 1)[0]
        if local in ROLE_ACCOUNTS:
            continue
        for first, last in people:
            for fmt in FORMATS:
                if render(fmt, first, last) == local:
                    return fmt
    return None


def candidates(first: str, last: str, domain: str, *, learned: str | None, country: str = "IN") -> list[str]:
    cfg = load_yaml("pipeline")
    priors = cfg["email_pattern_priors"].get(country, cfg["email_pattern_priors"]["default"])
    order = ([learned] if learned else []) + [f for f in priors if f != learned]
    out: list[str] = []
    for fmt in order:
        local = render(fmt, first, last)
        if local and f"{local}@{domain}" not in out:
            out.append(f"{local}@{domain}")
    return out[: cfg["max_email_candidates"]]
