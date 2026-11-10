"""
Error model.

Every expected failure raises a subclass of `AppError`. The API layer turns it
into an RFC 7807 "problem+json" response, so the frontend always receives the
same shape:

    {"type": "...", "title": "...", "status": 402, "detail": "...",
     "code": "quota_exceeded", "request_id": "..."}

Unexpected exceptions become a generic 500 (details go to the logs, never to
the client).
"""
from __future__ import annotations


class AppError(Exception):
    """Base class for all expected, user-facing errors."""

    status: int = 400
    code: str = "bad_request"
    title: str = "Bad request"

    def __init__(self, detail: str = "", *, code: str | None = None, extra: dict | None = None):
        super().__init__(detail or self.title)
        self.detail = detail or self.title
        if code:
            self.code = code
        self.extra = extra or {}


class NotFound(AppError):
    status, code, title = 404, "not_found", "Not found"


class Unauthorized(AppError):
    status, code, title = 401, "unauthorized", "Authentication required"


class Forbidden(AppError):
    status, code, title = 403, "forbidden", "Not allowed"


class Conflict(AppError):
    status, code, title = 409, "conflict", "Conflict"


class ValidationFailed(AppError):
    status, code, title = 422, "validation_failed", "Validation failed"


class QuotaExceeded(AppError):
    status, code, title = 402, "quota_exceeded", "Lead quota exceeded"


class BudgetExhausted(AppError):
    """Raised by the provider gateway when a paid call would exceed a budget cap."""

    status, code, title = 402, "budget_exhausted", "Budget exhausted"


class ProviderUnavailable(AppError):
    """A data/AI provider cannot be used right now (no key, circuit open, down)."""

    status, code, title = 503, "provider_unavailable", "Provider unavailable"
