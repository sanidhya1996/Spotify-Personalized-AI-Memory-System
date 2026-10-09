"""Error codes and the request tracking number.

abc.md:322 - "the gateway ... attaches a correlation identifier ... Errors
use stable codes for validation, authorization, policy denial, conflict,
dependency timeout, and retryable service failure."

"Stable" means the string never changes once published. Apps check
`code == "CONSENT_DENIED"`; they should never have to read English.
"""

import uuid
from contextvars import ContextVar

from fastapi.responses import JSONResponse

# The codes we return. One name per reason.
VALIDATION_FAILED = "VALIDATION_FAILED"
UNSUPPORTED_SCHEMA_VERSION = "UNSUPPORTED_SCHEMA_VERSION"
UNAUTHENTICATED = "UNAUTHENTICATED"
SUBJECT_MISMATCH = "SUBJECT_MISMATCH"
CONSENT_DENIED = "CONSENT_DENIED"
RATE_LIMITED = "RATE_LIMITED"
NOT_FOUND = "NOT_FOUND"
# abc.md:322 lists "conflict" among the stable error classes.
CONFLICT = "CONFLICT"
SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"

HEADER = "X-Correlation-Id"

# Holds the current request's id, so any function can read it without it
# being passed down through every call.
correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


def error(code: str, message: str) -> dict:
    """The body every error returns."""
    return {"code": code, "message": message, "correlation_id": correlation_id.get()}


async def add_correlation_id(request, call_next):
    """Give every request an id and send it back in the response.

    If the caller already sent one, keep theirs - that is how one trace
    follows a request across several services.
    """
    cid = request.headers.get(HEADER) or f"cid_{uuid.uuid4().hex[:16]}"
    token = correlation_id.set(cid)
    try:
        response = await call_next(request)
    finally:
        correlation_id.reset(token)
    response.headers[HEADER] = cid
    return response


async def handle_validation_error(request, exc) -> JSONResponse:
    """Give bad-input errors a stable code.

    FastAPI's own 422 body has no code, so a caller would have to parse
    prose. We name the fields that were wrong, but never repeat the values
    they sent - abc.md:322 wants identifiers and outcomes, not content.
    """
    fields = sorted({".".join(str(p) for p in e["loc"][1:]) for e in exc.errors()})
    return JSONResponse(
        status_code=422,
        content={
            "detail": error(
                VALIDATION_FAILED, f"invalid or missing fields: {', '.join(fields)}"
            )
        },
        headers={HEADER: correlation_id.get()},
    )


async def handle_http_error(request, exc) -> JSONResponse:
    """Put the tracking number into every error we raise ourselves."""
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail:
        detail = {**detail, "correlation_id": correlation_id.get()}
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": detail},
        headers={HEADER: correlation_id.get()},
    )
