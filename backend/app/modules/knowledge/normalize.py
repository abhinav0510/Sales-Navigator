"""
Normalisers - turn messy real-world values into stable keys.

These keys decide whether two records are the SAME company or person, so they
are deliberately conservative and fully unit-tested
(tests/unit/test_normalize.py).
"""
from __future__ import annotations

import re

import phonenumbers
import tldextract
from unidecode import unidecode

# Offline public-suffix list: tldextract never downloads anything at runtime.
_TLD = tldextract.TLDExtract(suffix_list_urls=())

# Legal-form suffixes stripped from company names before matching.
_LEGAL = re.compile(
    r"\b(private limited|pvt\.? ?ltd\.?|pvt|ltd\.?|limited|llp|llc|inc\.?|gmbh|opc|co\.?|company|corp\.?)\b", re.I)
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")

# Free-mail providers are never a company's domain.
FREE_MAIL = {"gmail.com", "yahoo.com", "yahoo.co.in", "outlook.com", "hotmail.com", "rediffmail.com",
             "icloud.com", "protonmail.com", "live.com"}


def registrable_domain(url_or_host: str) -> str | None:
    """'https://www.shop.kosh.co.in/about' -> 'kosh.co.in'.  Returns None for junk."""
    host = re.sub(r"^[a-z]+://", "", url_or_host.strip().lower()).split("/")[0].split(":")[0]
    ext = _TLD(host)
    if ext.domain and ext.suffix:
        return f"{ext.domain}.{ext.suffix}"
    # Our demo dataset uses the reserved ".example" TLD, which is not a public suffix.
    if host.endswith(".example"):
        parts = host.split(".")
        return ".".join(parts[-2:])
    return None


def company_name_key(name: str) -> str:
    """'Kosh Skincare Pvt. Ltd.' -> 'kosh skincare'."""
    s = unidecode(name).lower().replace("&", " and ")
    s = _LEGAL.sub(" ", s)
    s = _NON_ALNUM.sub(" ", s)
    return _SPACES.sub(" ", s).strip()


def person_name_key(name: str) -> str:
    """'Dr. Ananya  Kulkarni' -> 'ananya kulkarni' (honorifics removed)."""
    s = unidecode(name).lower()
    s = re.sub(r"\b(dr|mr|mrs|ms|shri|smt|ca|er|prof)\.?\s+", " ", s)
    s = _NON_ALNUM.sub(" ", s)
    return _SPACES.sub(" ", s).strip()


def split_name(full_name: str) -> tuple[str, str]:
    """('Ananya', 'Kulkarni'). Single-word names return (name, '')."""
    parts = person_name_key(full_name).split()
    if not parts:
        return "", ""
    return parts[0], parts[-1] if len(parts) > 1 else ""


def phone_e164(raw: str, default_region: str = "IN") -> str | None:
    """Parse and validate a phone number. Returns +E.164 or None if not a real number."""
    try:
        num = phonenumbers.parse(raw, default_region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_possible_number(num):
        return None
    return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)


def phone_type(e164: str) -> str:
    """mobile | landline | voip | unknown (from number-range metadata, offline)."""
    try:
        t = phonenumbers.number_type(phonenumbers.parse(e164))
    except phonenumbers.NumberParseException:
        return "unknown"
    return {
        phonenumbers.PhoneNumberType.MOBILE: "mobile",
        phonenumbers.PhoneNumberType.FIXED_LINE: "landline",
        phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE: "landline_or_mobile",
        phonenumbers.PhoneNumberType.VOIP: "voip",
    }.get(t, "unknown")
