"""
Structured JSON logging with mandatory PII redaction.

Every log line is one JSON object carrying correlation fields
(`request_id`, `workspace_id`, `search_id`, ...), so an operator can filter all
lines for one request or one search.

**Redaction is not optional.** The `redact_pii` processor runs on every event
before it is written. It masks email addresses and phone numbers anywhere in
the event (message or fields). A unit test (`tests/unit/test_logging_redaction.py`)
guarantees no raw email or phone reaches a log sink.
"""
from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog

_EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
# International-looking phone numbers: optional +, 10-15 digits with separators.
_PHONE_RE = re.compile(r"(\+?\d[\d\s\-()]{8,}\d)")


def _mask_phone(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group(1))
    if len(digits) < 10:
        return match.group(1)
    return ("+" if match.group(1).startswith("+") else "") + "*" * (len(digits) - 4) + digits[-4:]


def redact_text(value: str) -> str:
    """Mask emails (r***@acme.in) and phones (+91******3210) inside a string."""
    value = _EMAIL_RE.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", value)
    return _PHONE_RE.sub(_mask_phone, value)


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: _redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    return value


def redact_pii(_logger: Any, _method: str, event_dict: dict) -> dict:
    """structlog processor: redact every string value in the event."""
    return {k: _redact(v) for k, v in event_dict.items()}


def configure_logging(level: str = "INFO") -> None:
    """Call once at process start (API and worker)."""
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,   # request_id etc. bound per request
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_pii,                                 # ALWAYS before rendering
            structlog.processors.JSONRenderer(sort_keys=True),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "sn") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
