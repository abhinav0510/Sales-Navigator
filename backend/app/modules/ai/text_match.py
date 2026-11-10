"""
Keyword matching used by scoring. Whole words only: "ev" must match "EV charging"
but not "every" or "event"; multi-word terms ("family office") match as a phrase.
"""
from __future__ import annotations

import re
from functools import lru_cache


@lru_cache(maxsize=4096)
def _pattern(term: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z0-9])" + re.escape(term.lower().strip()) + r"(?![a-z0-9])")


def mentions(text: str, term: str) -> bool:
    """True if `term` appears in `text` as a whole word or phrase (case-insensitive)."""
    return bool(term.strip()) and bool(_pattern(term).search(text.lower()))
