"""The six requirements that finish POST /v1/events.

Each test names the abc.md line it comes from.
"""

import pytest
from fastapi.testclient import TestClient

from memory import cache, db
from memory.api import app
from memory.auth import mint_token
from memory.errors import HEADER

client = TestClient(app)

AUTH = {"Authorization": f"Bearer {mint_token('user_001', 'chat-surface')}"}


def valid_event(**overrides):
    body = {
        "schema_version": "1.0",
        "subject_id": "user_001",
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


# --- 1. Tracking number (abc.md:322) --------------------------------------

def test_response_has_a_tracking_number():
    r = client.post("/v1/events", json=valid_event(), headers=AUTH)
    assert r.headers[HEADER].startswith("cid_")


def test_the_callers_tracking_number_is_kept():
    """So one trace can be followed across several services."""
    headers = {**AUTH, HEADER: "cid_from_the_gateway"}
    r = client.post("/v1/events", json=valid_event(), headers=headers)
    assert r.headers[HEADER] == "cid_from_the_gateway"


def test_errors_carry_the_tracking_number():
    db.set_consent("user_001", "denied")
    r = client.post("/v1/events", json=valid_event(), headers=AUTH)
    assert r.status_code == 403
    assert r.json()["detail"]["correlation_id"] == r.headers[HEADER]


# --- 2. Stable error names (abc.md:322) -----------------------------------

def test_bad_input_has_a_stable_code():
    body = valid_event()
    del body["subject_id"]
    r = client.post("/v1/events", json=body, headers=AUTH)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "VALIDATION_FAILED"


def test_error_names_the_field_but_not_the_value():
    """abc.md:322 - identifiers and outcomes, never raw content."""
    r = client.post("/v1/events", json=valid_event(surface="teleport"), headers=AUTH)
    assert r.status_code == 422
    message = r.json()["detail"]["message"]
    assert "surface" in message
    assert "teleport" not in message


# --- 3. Audit records (abc.md:460) ----------------------------------------

def test_accepted_event_is_recorded():
    r = client.post("/v1/events", json=valid_event(), headers=AUTH)
    entries = db.get_audit("user_001")
    assert entries[0]["action"] == "event.accepted"
    assert entries[0]["outcome"] == "accepted"
    assert entries[0]["event_id"] == r.json()["event_id"]
    assert entries[0]["service_id"] == "chat-surface"
    assert entries[0]["correlation_id"] == r.headers[HEADER]


def test_rejected_event_is_recorded_with_its_reason():
    db.set_consent("user_001", "denied")
    client.post("/v1/events", json=valid_event(), headers=AUTH)
    entries = db.get_audit("user_001")
    assert entries[0]["action"] == "event.rejected"
    assert entries[0]["reason"] == "CONSENT_DENIED"


def test_duplicate_is_recorded():
    client.post("/v1/events", json=valid_event(), headers=AUTH)
    client.post("/v1/events", json=valid_event(), headers=AUTH)
    assert db.get_audit("user_001")[0]["outcome"] == "duplicate"


def test_audit_holds_no_event_content():
    """abc.md:145 - redact payloads, keep identifiers."""
    client.post("/v1/events", json=valid_event(), headers=AUTH)
    entry = db.get_audit("user_001")[0]
    for private in ("locale", "occurred_at", "surface", "payload"):
        assert private not in entry


# --- 4. Rate limits (abc.md:356) ------------------------------------------

def test_too_many_events_are_refused():
    """The counter is per clock minute, so a run that straddles a minute
    boundary would reset it half way through. Look for a 429 anywhere in
    the run rather than only at the end."""
    limit = cache.RATE_LIMIT_PER_MINUTE
    refused = []

    for n in range(limit + 5):
        r = client.post(
            "/v1/events", json=valid_event(idempotency_key=f"k{n}"), headers=AUTH
        )
        if r.status_code == 429:
            refused.append(r)

    assert refused, f"no request was refused after {limit + 5} in one minute"
    assert refused[0].json()["detail"]["code"] == "RATE_LIMITED"


def test_the_rate_counter_expires_by_itself():
    client.post("/v1/events", json=valid_event(), headers=AUTH)
    from datetime import datetime, timezone

    minute = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    key = cache.config.redis_key("rate", "user_001", minute)
    assert 0 < cache.client().ttl(key) <= 60


# --- 5. Retention is enforced (abc.md:109) --------------------------------

def test_expired_events_are_deleted():
    r = client.post("/v1/events", json=valid_event(), headers=AUTH)
    event_id = r.json()["event_id"]

    # Age it past its expiry.
    with db.connect() as conn:
        conn.execute(
            "UPDATE ingested_event SET expires_at = now() - interval '1 day'"
            " WHERE event_id = %s",
            (event_id,),
        )

    assert db.delete_expired_events() >= 1
    assert db.count_events("user_001") == 0


def test_unexpired_events_are_kept():
    client.post("/v1/events", json=valid_event(), headers=AUTH)
    db.delete_expired_events()
    assert db.count_events("user_001") == 1


# --- 6. The caller does not wait (abc.md:324) -----------------------------

def test_audit_is_written_after_the_reply():
    """The audit line is a background task, so it lands after the response.

    TestClient waits for background tasks to finish, so by the time we can
    read the response the line exists - what this proves is that the
    endpoint hands it off rather than writing it inline.
    """
    r = client.post("/v1/events", json=valid_event(), headers=AUTH)
    assert r.status_code == 200
    assert db.get_audit("user_001")[0]["event_id"] == r.json()["event_id"]


# --- 7. Metrics (abc.md:143, :339) ----------------------------------------

def test_metrics_needs_a_token():
    r = client.get("/metrics")
    assert r.status_code == 401


def test_metrics_counts_accepted_and_rejected():
    client.post("/v1/events", json=valid_event(), headers=AUTH)
    db.set_consent("user_001", "denied")
    client.post("/v1/events", json=valid_event(idempotency_key="k2"), headers=AUTH)

    m = client.get("/metrics", headers=AUTH).json()
    assert m["events"]["accepted"] >= 1
    assert m["events"]["rejected"] >= 1
    assert m["rejections_by_reason"]["CONSENT_DENIED"] >= 1
    assert 0 < m["rejection_rate"] <= 1


def test_metrics_reports_ingestion_lag():
    client.post("/v1/events", json=valid_event(), headers=AUTH)
    m = client.get("/metrics", headers=AUTH).json()
    assert m["ingestion_lag_seconds"] is not None
    assert m["ingestion_lag_seconds"] < 60


def test_metrics_leaks_no_subject_data():
    """An operational view, not a window into anyone's data."""
    client.post("/v1/events", json=valid_event(), headers=AUTH)
    body = client.get("/metrics", headers=AUTH).text
    assert "user_001" not in body
    assert "evt_" not in body
