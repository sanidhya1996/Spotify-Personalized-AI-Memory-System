"""Why this file exists
=====================

tests/test_extraction.py tests the rules. This file tests the endpoint
around them: the token, the subject binding, consent, a missing event, and
what happens when the model cannot be reached.

The model is replaced with a stand-in on every test. The free tier allows
five requests a minute, so a suite that called it for real would fail on
the sixth test - and would give different answers each run.
"""

import pytest
from fastapi.testclient import TestClient

from memory import db, model_client
from memory.api import app
from memory.auth import mint_token

client = TestClient(app)

AUTH_1 = {"Authorization": f"Bearer {mint_token('user_001', 'chat-surface')}"}
AUTH_2 = {"Authorization": f"Bearer {mint_token('user_002', 'chat-surface')}"}


# A recorded answer, of the shape the model really returns. Taken from an
# actual response to "I really prefer instrumental music when I'm working".
RECORDED = [
    {
        "memory_type": "explicit_preference",
        "fact": "Prefers instrumental music while working",
        "entities": ["instrumental music", "working"],
        "confidence": 0.95,
        "reason": "The listener explicitly stated a lasting preference.",
    }
]


@pytest.fixture
def fake_model(monkeypatch):
    """Replace the model with a fixed answer. Returns a setter."""
    def use(proposals=RECORDED, error=None):
        def stand_in(event):
            if error is not None:
                raise model_client.ModelUnavailable(error)
            return proposals
        monkeypatch.setattr(model_client, "propose_candidates", stand_in)
    use()
    return use


def store_event(content="I prefer instrumental music", subject="user_001",
                event_type="ai_interaction", key="extract_key"):
    """Put a real event in the database and return its id."""
    auth = AUTH_1 if subject == "user_001" else AUTH_2
    r = client.post(
        "/v1/events",
        json={
            "schema_version": "1.0",
            "subject_id": subject,
            "event_type": event_type,
            "surface": "chat",
            "locale": "en-US",
            "occurred_at": "2026-09-24T10:00:00Z",
            "consent_state": "granted",
            "source_event_id": "src_1",
            "idempotency_key": key,
            "content": content,
        },
        headers=auth,
    )
    assert r.status_code == 200, r.json()
    return r.json()["event_id"]


def extract(event_id, subject="user_001", headers=None):
    return client.post(
        "/v1/memories/extract",
        json={"subject_id": subject, "event_id": event_id},
        headers=headers or (AUTH_1 if subject == "user_001" else AUTH_2),
    )


# --- The happy path -------------------------------------------------------

def test_an_event_becomes_a_candidate_memory(fake_model):
    r = extract(store_event())
    assert r.status_code == 200

    body = r.json()
    assert body["no_memory"] is False
    assert body["candidates"][0]["memory_type"] == "explicit_preference"
    assert body["candidates"][0]["confidence"] == 0.95


def test_the_result_names_the_event_it_came_from(fake_model):
    event_id = store_event()
    assert extract(event_id).json()["event_id"] == event_id


# --- Security: the same rules as every other endpoint ---------------------

def test_a_token_is_required(fake_model):
    r = client.post(
        "/v1/memories/extract",
        json={"subject_id": "user_001", "event_id": "evt_x"},
    )
    assert r.status_code == 401


def test_one_subject_cannot_extract_for_another(fake_model):
    """abc.md:162 - every read binds to the authenticated subject."""
    event_id = store_event()
    r = extract(event_id, subject="user_001", headers=AUTH_2)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "SUBJECT_MISMATCH"


def test_knowing_an_event_id_is_not_enough(fake_model):
    """user_002 asks for their own subject, but quotes user_001's event."""
    event_id = store_event(subject="user_001")
    r = extract(event_id, subject="user_002")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "NOT_FOUND"


# --- Consent is checked again here ----------------------------------------

def test_consent_is_rechecked_at_extraction(fake_model):
    """abc.md:53 - enforced before memory reaches retrieval, not only at
    the door. Consent can be withdrawn between capture and extraction."""
    event_id = store_event()
    db.set_consent("user_001", "paused")

    r = extract(event_id)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "CONSENT_DENIED"


# --- When the model cannot be reached -------------------------------------

def test_a_model_outage_is_reported_not_invented(fake_model):
    """abc.md:158 - degrade gracefully. Never invent a memory."""
    event_id = store_event()
    fake_model(error="429 RESOURCE_EXHAUSTED")

    r = extract(event_id)
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "SERVICE_UNAVAILABLE"


def test_nothing_is_stored_when_the_model_fails(fake_model):
    event_id = store_event()
    fake_model(error="down")
    extract(event_id)
    assert db.get_audit("user_001")[0]["reason"] == "SERVICE_UNAVAILABLE"


# --- No memory is an answer, not a failure --------------------------------

def test_no_memory_is_a_normal_200(fake_model):
    """abc.md:341 - return "no memory" when evidence is insufficient."""
    event_id = store_event(content="hi")
    fake_model(proposals=[])

    r = extract(event_id)
    assert r.status_code == 200
    assert r.json()["no_memory"] is True
    assert r.json()["candidates"] == []


def test_a_rejected_proposal_is_reported(fake_model):
    """abc.md:296 - rejections are visible, not silently swallowed."""
    event_id = store_event()
    fake_model(proposals=[{"memory_type": "mood", "fact": "seems sad",
                           "confidence": 0.9}])

    body = extract(event_id).json()
    assert body["no_memory"] is True
    assert "unknown memory_type" in body["rejected"][0]


# --- The audit trail ------------------------------------------------------

def test_extraction_is_audited(fake_model):
    event_id = store_event()
    extract(event_id)

    entry = db.get_audit("user_001")[0]
    assert entry["action"] == "extract.completed"
    assert entry["outcome"] == "extracted"
    assert entry["event_id"] == event_id


def test_the_audit_holds_no_extracted_text(fake_model):
    """abc.md:322 - identifiers and outcomes, never private content."""
    extract(store_event(content="i only listen to sad music alone"))
    assert "sad music" not in str(db.get_audit("user_001")[0])
