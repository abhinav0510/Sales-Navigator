"""
Identifier helpers.

We use UUIDv7 everywhere: it is globally unique *and* time-ordered, so new rows
land at the end of B-tree indexes (fast inserts) and IDs sort by creation time.
"""
from __future__ import annotations

import uuid


def new_id() -> uuid.UUID:
    """Return a new time-ordered UUID (v7)."""
    return uuid.uuid7()
