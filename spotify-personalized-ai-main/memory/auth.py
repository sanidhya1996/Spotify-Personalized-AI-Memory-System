"""Authentication and subject binding.

§5.5 Security: "Every read and write must bind to an authenticated subject
and service identity."

Think of a cinema ticket. The token says which subject it is for, and it
carries a stamp only this server can make. We do not keep a list of valid
tokens — we check the stamp and read the subject out of the token itself.
If someone edits the subject, the stamp no longer matches and we refuse.

There is no login here on purpose. The callers are Spotify's AI surfaces
(services), not people. In production the API gateway mints these tokens
after verifying the listener's existing Spotify session; for the pilot we
mint them ourselves with the same secret.
"""

import os
from datetime import datetime, timedelta, timezone

import jwt
from dotenv import load_dotenv
from fastapi import Header, HTTPException
from pydantic import BaseModel

load_dotenv()

# The one secret in .env. Everything else is derived from it.
SECRET = os.environ.get("MEMORY_JWT_SECRET")
ALGORITHM = "HS256"

# Tokens are short-lived, which is what defends against the "replay of
# stale tokens" in the Security Lead's threat model.
TOKEN_LIFETIME = timedelta(minutes=15)


class Caller(BaseModel):
    """Who is making this request, once the token has been checked."""

    subject_id: str
    service_id: str


def _unauthenticated(message: str) -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={"code": "UNAUTHENTICATED", "message": message},
    )


def mint_token(subject_id: str, service_id: str) -> str:
    """Stamp a ticket for one subject.

    In production the gateway does this after confirming the listener's
    Spotify session. Here it is used for the demo and the tests.
    """
    if not SECRET:
        raise RuntimeError("MEMORY_JWT_SECRET is not set; copy .env.example to .env")

    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject_id,
        "svc": service_id,
        "iat": now,
        "exp": now + TOKEN_LIFETIME,
    }
    return jwt.encode(payload, SECRET, algorithm=ALGORITHM)


def authenticate(authorization: str | None = Header(default=None)) -> Caller:
    """Check the stamp, then read the subject out of the token.

    Used as a FastAPI dependency, so every endpoint that declares it is
    unreachable without a valid, unexpired token.
    """
    if not SECRET:
        raise RuntimeError("MEMORY_JWT_SECRET is not set; copy .env.example to .env")

    if not authorization or not authorization.startswith("Bearer "):
        raise _unauthenticated("Authorization: Bearer <token> is required")

    token = authorization.removeprefix("Bearer ").strip()

    try:
        payload = jwt.decode(token, SECRET, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise _unauthenticated("token has expired") from None
    except jwt.InvalidTokenError:
        # Covers a bad signature, which is what an edited subject produces.
        raise _unauthenticated("token is not valid") from None

    subject_id = payload.get("sub")
    service_id = payload.get("svc")
    if not subject_id or not service_id:
        raise _unauthenticated("token is missing subject or service")

    return Caller(subject_id=subject_id, service_id=service_id)


def bind_subject(caller: Caller, subject_id: str) -> None:
    """Refuse to let one subject's token touch another subject's data.

    This is the cross-subject isolation check. §9 makes it pass/fail: if
    user A's memory can reach user B, nothing else about the system matters.
    """
    if caller.subject_id != subject_id:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "SUBJECT_MISMATCH",
                "message": "token is not authorized for this subject",
            },
        )
