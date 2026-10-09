"""Check every endpoint exists and responds.

These tests do not need the server running. TestClient starts the app
in-process, sends a real request, and gives back the real response.

Every endpoint except /health needs a token — see tests/test_auth.py for
the authentication and cross-subject isolation rules themselves.
"""

from fastapi.testclient import TestClient

from memory.api import app
from memory.auth import mint_token

client = TestClient(app)

AUTH = {"Authorization": f"Bearer {mint_token('user_001', 'chat-surface')}"}


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# --- Write path -----------------------------------------------------------

def test_create_event():
    # Fully validated against the Event model — see tests/test_events.py
    # for the full behaviour of this endpoint.
    r = client.post(
        "/v1/events",
        json={
            "schema_version": "1.0",
            "subject_id": "user_001",
            "event_type": "playback",
            "surface": "player",
            "locale": "en-US",
            "occurred_at": "2026-09-23T10:00:00Z",
            "consent_state": "granted",
            "source_event_id": "src_api",
            "idempotency_key": "test_api_key",
        },
        headers=AUTH,
    )
    assert r.status_code == 200
    assert r.json()["accepted"] is True


def test_extract_memories():
    # Now a typed request needing subject_id and event_id, and the event
    # must exist. See tests/test_extract_api.py for its real behaviour.
    r = client.post(
        "/v1/memories/extract",
        json={"subject_id": "user_001", "event_id": "evt_does_not_exist"},
        headers=AUTH,
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "NOT_FOUND"


def test_create_memory():
    # Now a typed request. See tests/test_memories_api.py for its real
    # behaviour; this only checks the endpoint is reachable and validates.
    r = client.post(
        "/v1/memories",
        json={"subject_id": "user_001", "memory_type": "episode",
              "fact": "Played a focus playlist"},
        headers=AUTH,
    )
    assert r.status_code == 200
    assert "memory_id" in r.json()


# --- Read path ------------------------------------------------------------

def test_search_memories():
    # Now a typed request needing an intent. See tests/test_search_api.py
    # for ranking, policy filtering and diversity.
    r = client.post(
        "/v1/memories/search",
        json={"subject_id": "user_001", "intent": "something for working"},
        headers=AUTH,
    )
    assert r.status_code == 200
    assert "results" in r.json()


def test_compose_context():
    # Now a typed request. See tests/test_compose_api.py for the package
    # shape, the budget, and the prompt-injection defences.
    r = client.post(
        "/v1/context/compose",
        json={"subject_id": "user_001", "intent": "something while I work"},
        headers=AUTH,
    )
    assert r.status_code == 200
    assert "context_block" in r.json()


# --- Correction and deletion ---------------------------------------------

def test_update_memory():
    # Now typed, and needs a real memory. See tests/test_correct_delete_api.py
    # for optimistic concurrency, correcting and expiring.
    r = client.patch(
        "/v1/memories/mem_does_not_exist",
        json={"subject_id": "user_001", "operation": "expire",
              "expected_version": 1},
        headers=AUTH,
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "NOT_FOUND"


def test_delete_memory():
    # Now needs the subject and a real memory. See
    # tests/test_correct_delete_api.py for the cross-store deletion.
    r = client.delete(
        "/v1/memories/mem_does_not_exist?subject_id=user_001", headers=AUTH
    )
    assert r.status_code == 404


def test_get_deletion():
    # Now subject-scoped, and only real jobs exist.
    r = client.get("/v1/deletions/job_1?subject_id=user_001", headers=AUTH)
    assert r.status_code == 404


# --- Feedback and explainability -----------------------------------------

def test_create_feedback():
    # Now typed. See tests/test_feedback_trace_api.py for the rule that
    # positive feedback on our own guess does not reinforce it.
    r = client.post(
        "/v1/feedback",
        json={"subject_id": "user_001", "kind": "experience",
              "sentiment": "unhelpful"},
        headers=AUTH,
    )
    assert r.status_code == 200
    assert r.json()["recorded"] is True


def test_get_trace():
    # Now subject-scoped, and only real traces exist.
    r = client.get("/v1/traces/cid_nope?subject_id=user_001", headers=AUTH)
    assert r.status_code == 404
