"""
FastAPI application - the APPLICATION tier's HTTP entry point.

    Presentation tier (Next.js, :3000)  --HTTP/JSON-->  this app (:8000)  --SQL-->  PostgreSQL

Cross-cutting concerns wired here (and only here):
  * request id      - every request gets an X-Request-ID (echoed in responses + logs)
  * structured logs - one JSON line per request, PII redacted
  * metrics         - Prometheus counters/histograms, scraped at GET /metrics
  * errors          - AppError -> RFC 7807 problem+json; anything else -> 500,
                      recorded in error_event for the admin Errors view
  * health          - /healthz (process up), /readyz (database reachable)

Run:  uvicorn app.main:app --reload --port 8000
"""
from __future__ import annotations

import time
import uuid

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.api.routes import router
from app.core.config import get_settings
from app.core.db import get_engine
from app.core.errors import AppError
from app.core.logging import configure_logging, get_logger
from app.core.metrics import HTTP_LATENCY, HTTP_REQUESTS
from app.modules.observability.recorder import record_error

configure_logging()
log = get_logger("api")

app = FastAPI(title="Sales Navigator API", version="0.1.0",
              description="Application tier: lead discovery, enrichment, quality judging and billing.")
app.include_router(router)


def _problem(status: int, code: str, title: str, detail: str, request_id: str, extra: dict | None = None) -> JSONResponse:
    body = {"type": f"https://errors.salesnavigator.example/{code}", "title": title, "status": status,
            "detail": detail, "code": code, "request_id": request_id}
    if extra:
        body["extra"] = extra
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id, path=request.url.path)
    request.state.request_id = request_id
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        # Unexpected error: log it, record it for the admin Errors view, and answer with
        # problem+json. Handling it HERE (inside the CORS middleware) means the browser
        # can read the error and its request id instead of seeing an opaque network failure.
        log.exception("unhandled_error")
        record_error(location=f"api {request.method} {request.url.path}", error=exc, request_id=request_id)
        response = _problem(500, "internal_error", "Internal error",
                            "Something went wrong. Quote the request id to support.", request_id)
    elapsed = time.perf_counter() - started
    route = request.scope.get("route")
    route_path = getattr(route, "path", "unmatched")
    HTTP_REQUESTS.labels(route_path, request.method, str(response.status_code)).inc()
    HTTP_LATENCY.labels(route_path).observe(elapsed)
    response.headers["X-Request-ID"] = request_id
    log.info("http_request", method=request.method, status=response.status_code, ms=round(elapsed * 1000, 1))
    return response


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return _problem(exc.status, exc.code, exc.title, exc.detail, request.state.request_id, exc.extra)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return _problem(422, "validation_failed", "Validation failed", "Request body or parameters are invalid",
                    request.state.request_id, {"errors": exc.errors()[:10]})


# Middleware order matters: CORS is added LAST so it is the OUTERMOST layer and adds
# its headers to every response - including the 500s produced by request_context.
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origin_list, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"], expose_headers=["X-Request-ID"])


@app.get("/healthz", tags=["ops"])
def healthz():
    return {"status": "ok"}


@app.get("/readyz", tags=["ops"])
def readyz():
    with get_engine("app").connect() as conn:
        conn.execute(text("SELECT 1"))
    s = get_settings()
    # Say plainly whether each mode can actually work (never return key values).
    return {"status": "ready", "data_mode": s.data_mode, "llm_mode": s.llm_mode,
            "brave_key_set": bool(s.brave_api_key), "anthropic_key_set": bool(s.anthropic_api_key),
            "warnings": [w for w, bad in (
                ("SN_DATA_MODE=live but SN_BRAVE_API_KEY is empty: web search is off", s.data_mode == "live" and not s.brave_api_key),
                ("SN_LLM_MODE=claude but ANTHROPIC_API_KEY is empty", s.llm_mode == "claude" and not s.anthropic_api_key),
            ) if bad]}


@app.get("/metrics", tags=["ops"])
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
