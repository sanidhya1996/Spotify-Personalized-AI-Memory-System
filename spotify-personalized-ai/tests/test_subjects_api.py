"""Why this file exists
=====================

A new listener has to be usable without anyone editing code or running a
script: they switch memory on once, and from then on everything works.
These tests follow one brand-new subject through that, and check the
subject list the console's picker is built from.

abc.md:53 - consent is enforced before memory is used, so a new subject is
refused until they switch memory on.
abc.md:340 - the console only looks at subjects that have a consent record.
"""

import pytest
from fastapi.testclient import TestClient

from memory import db
from memory.api import app
from memory.auth import mint_token

client = TestClient(app)
NEW = "new_listener_test"
AUTH_NEW = {"Authorization": f"Bearer {mint_token(NEW, 'memory-console')}"}


@pytest.fixture(autouse=True)
def forget_new_subject():
    def remove():
        db.delete_events(NEW)
        db.delete_audit(NEW)
        with db.connect() as conn:
            conn.execute("DELETE FROM consent WHERE subject_id = %s", (NEW,))

    remove()
    yield
    remove()


# Send one event as the new subject.
def send_event():
    return client.post("/v1/events", headers=AUTH_NEW, json={
        "schema_version": "1.0", "subject_id": NEW, "event_type": "ai_interaction",
        "surface": "chat", "locale": "en-US", "occurred_at": "2026-09-30T10:00:00Z",
        "consent_state": "granted", "source_event_id": "src_new",
        "idempotency_key": "new_key_1", "content": "I like jazz",
    })


# A subject nobody has set up says so, instead of claiming "granted".
def test_a_new_subject_reads_not_set():
    r = client.get("/v1/consent", params={"subject_id": NEW}, headers=AUTH_NEW)
    assert r.status_code == 200
    assert r.json()["state"] == "not_set"


# Refused before switching memory on; accepted straight after.
def test_switching_memory_on_is_all_a_new_subject_needs():
    assert send_event().status_code == 403

    r = client.patch("/v1/consent", headers=AUTH_NEW,
                     json={"subject_id": NEW, "state": "granted"})
    assert r.status_code == 200

    assert send_event().status_code == 200


# The new subject appears in the picker's list once they have a record.
def test_the_subject_list_includes_new_subjects():
    ids = [s["subject_id"] for s in client.get("/subjects", headers=AUTH_NEW).json()["subjects"]]
    assert NEW not in ids

    client.patch("/v1/consent", headers=AUTH_NEW, json={"subject_id": NEW, "state": "granted"})

    subjects = client.get("/subjects", headers=AUTH_NEW).json()["subjects"]
    new = next(s for s in subjects if s["subject_id"] == NEW)
    assert new["consent"] == "granted"
    assert new["cohort"] == "memory_enabled"


# Seeded subjects carry their consent and experiment group; golden ones are hidden.
def test_the_subject_list_shows_state_and_hides_golden_subjects():
    db.set_consent("golden_test_hidden", "granted")
    try:
        subjects = {s["subject_id"]: s for s in
                    client.get("/subjects", headers=AUTH_NEW).json()["subjects"]}
    finally:
        with db.connect() as conn:
            conn.execute("DELETE FROM consent WHERE subject_id = 'golden_test_hidden'")

    assert "golden_test_hidden" not in subjects
    assert subjects["user_003"]["cohort"] == "memory_disabled"
    assert subjects["user_004"]["consent"] == "denied"
    assert set(subjects["user_001"]) == {"subject_id", "consent", "updated_at", "cohort",
                                         "region", "age_band"}


# The list needs a valid token like everything else.
def test_the_subject_list_requires_a_token():
    assert client.get("/subjects").status_code == 401
