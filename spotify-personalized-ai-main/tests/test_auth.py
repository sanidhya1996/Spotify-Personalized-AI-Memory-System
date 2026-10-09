"""Authentication and cross-subject isolation.

§5.5: every read and write binds to an authenticated subject and service.
§9: a cross-subject isolation failure blocks release regardless of score,
so these are the most important tests in the suite.
"""

import pytest
from fastapi.testclient import TestClient

from memory import db
from memory.api import app
from memory.auth import mint_token

client = TestClient(app)


def auth_for(subject_id: str, service_id: str = "chat-surface") -> dict:
    return {"Authorization": f"Bearer {mint_token(subject_id, service_id)}"}


USER_1 = auth_for("user_001")
USER_2 = auth_for("user_002")

# Every endpoint except /health, as (method, path).
PROTECTED = [
    ("post", "/v1/events"),
    ("post", "/v1/memories/extract"),
    ("post", "/v1/memories"),
    ("post", "/v1/memories/search"),
    ("post", "/v1/context/compose"),
    ("patch", "/v1/memories/mem_1"),
    ("delete", "/v1/memories/mem_1"),
    ("get", "/v1/deletions/job_1"),
    ("post", "/v1/feedback"),
    ("get", "/v1/traces/trc_1"),
]


def call(method, path, headers=None):
    """Send a request. GET and DELETE take no JSON body."""
    kwargs = {"headers": headers} if headers else {}
    if method in ("post", "patch"):
        kwargs["json"] = {}
    return getattr(client, method)(path, **kwargs)


def event_for(subject_id: str, **overrides):
    body = {
        "schema_version": "1.0",
        "subject_id": subject_id,
        "event_type": "playback",
        "surface": "player",
        "locale": "en-US",
        "occurred_at": "2026-09-23T10:00:00Z",
        "consent_state": "granted",
        "source_event_id": "src_1",
        "idempotency_key": "key_1",
    }
    body.update(overrides)
    return body


# --- Every endpoint is protected -----------------------------------------

@pytest.mark.parametrize("method,path", PROTECTED)
def test_no_token_is_rejected(method, path):
    r = call(method, path)
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "UNAUTHENTICATED"


@pytest.mark.parametrize("method,path", PROTECTED)
def test_unknown_token_is_rejected(method, path):
    bad = {"Authorization": "Bearer not.a.real.token"}
    r = call(method, path, headers=bad)
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "UNAUTHENTICATED"


@pytest.mark.parametrize("method,path", PROTECTED)
def test_malformed_header_is_rejected(method, path):
    bad = {"Authorization": mint_token("user_001", "chat")}  # no "Bearer "
    r = call(method, path, headers=bad)
    assert r.status_code == 401


def test_health_stays_public():
    r = client.get("/health")
    assert r.status_code == 200


# --- Cross-subject isolation — the pass/fail requirement ------------------

def test_token_cannot_write_for_another_subject():
    r = client.post("/v1/events", json=event_for("user_001"), headers=USER_2)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "SUBJECT_MISMATCH"
    assert db.count_events("user_001") == 0


def test_token_can_write_for_its_own_subject():
    r = client.post("/v1/events", json=event_for("user_002"), headers=USER_2)
    assert r.status_code == 200
    assert db.count_events("user_002") == 1


def test_subject_check_runs_before_anything_is_stored():
    # Even a perfectly valid event is not stored for the wrong subject.
    client.post("/v1/events", json=event_for("user_001"), headers=USER_2)
    assert db.count_events("user_001") == 0
    assert db.count_events("user_002") == 0


def test_each_subject_writes_only_its_own_events():
    client.post(
        "/v1/events",
        json=event_for("user_001", idempotency_key="k1"),
        headers=USER_1,
    )
    client.post(
        "/v1/events",
        json=event_for("user_002", idempotency_key="k2"),
        headers=USER_2,
    )
    assert db.count_events("user_001") == 1
    assert db.count_events("user_002") == 1


# --- The stamp cannot be forged ------------------------------------------

def test_tampered_subject_is_rejected():
    """Edit the subject inside the token and the signature stops matching."""
    import jwt

    token = mint_token("user_002", "chat-surface")
    payload = jwt.decode(token, options={"verify_signature": False})
    payload["sub"] = "user_001"  # try to become someone else

    forged = jwt.encode(payload, "the-wrong-secret", algorithm="HS256")
    r = client.post(
        "/v1/events",
        json=event_for("user_001"),
        headers={"Authorization": f"Bearer {forged}"},
    )
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "UNAUTHENTICATED"
    assert db.count_events("user_001") == 0


def test_expired_token_is_rejected():
    """Stale tokens are refused — the replay defence."""
    from datetime import datetime, timedelta, timezone

    import jwt

    from memory.auth import ALGORITHM, SECRET

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    expired = jwt.encode(
        {"sub": "user_001", "svc": "chat-surface", "exp": past},
        SECRET,
        algorithm=ALGORITHM,
    )
    r = client.post(
        "/v1/events",
        json=event_for("user_001"),
        headers={"Authorization": f"Bearer {expired}"},
    )
    assert r.status_code == 401
    assert "expired" in r.json()["detail"]["message"]


def test_token_carries_the_service_identity():
    """§5.5 requires subject AND service identity."""
    from memory.auth import authenticate

    caller = authenticate(f"Bearer {mint_token('user_003', 'player-surface')}")
    assert caller.subject_id == "user_003"
    assert caller.service_id == "player-surface"


# --- Isolation behind the door -------------------------------------------

def test_same_idempotency_key_is_independent_per_subject():
    """Idempotency keys are chosen by callers, so two subjects will collide.

    §7.2 step 5 requires subject partition keys. Without them, the second
    subject gets the first subject's event_id and their event is dropped.
    """
    key = "same_key_from_both"

    r1 = client.post(
        "/v1/events",
        json=event_for("user_001", idempotency_key=key),
        headers=USER_1,
    )
    r2 = client.post(
        "/v1/events",
        json=event_for("user_002", idempotency_key=key),
        headers=USER_2,
    )

    assert r1.status_code == r2.status_code == 200

    # Neither is a duplicate of the other.
    assert r1.json()["duplicate"] is False
    assert r2.json()["duplicate"] is False

    # No identifier leaks between subjects.
    assert r1.json()["event_id"] != r2.json()["event_id"]

    # Both events survive.
    assert db.count_events("user_001") == 1
    assert db.count_events("user_002") == 1


def test_repeat_within_one_subject_still_deduplicates():
    """Partitioning must not break idempotency for a single subject."""
    key = "repeated_key"

    first = client.post(
        "/v1/events", json=event_for("user_001", idempotency_key=key), headers=USER_1
    )
    second = client.post(
        "/v1/events", json=event_for("user_001", idempotency_key=key), headers=USER_1
    )

    assert first.json()["event_id"] == second.json()["event_id"]
    assert second.json()["duplicate"] is True
    assert db.count_events("user_001") == 1
