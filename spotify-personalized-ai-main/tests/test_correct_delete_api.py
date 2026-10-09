"""Why this file exists
=====================

Endpoints 6, 7 and 8 are where a listener takes control back: correcting
what the system believes, and removing it.

abc.md scoring makes a deletion failure block release regardless of
everything else, which puts these tests in the same class as the
cross-subject ones. A deletion that reports success while leaving data
behind is the worst outcome in the project.

abc.md:313 - "Correct, supersede, expire, or change an eligible memory
              under optimistic concurrency."
abc.md:315 - "Start cross-store deletion and return a traceable job
              identifier."
abc.md:317 - "Report graph, vector, cache, operational-store, and
              backup-policy status."
"""

import pytest
from fastapi.testclient import TestClient

from memory import db, deletion, embeddings, graph
from memory.api import app
from memory.auth import mint_token

client = TestClient(app)
AUTH_1 = {"Authorization": f"Bearer {mint_token('user_001', 'chat-surface')}"}
AUTH_2 = {"Authorization": f"Bearer {mint_token('user_002', 'chat-surface')}"}


@pytest.fixture(autouse=True)
def clean():
    graph.ensure_constraints()
    embeddings.ensure_index()
    for subject in ("user_001", "user_002"):
        graph.delete_memories(subject)
        db.set_consent(subject, "granted")
    with db.connect() as conn:
        conn.execute("DELETE FROM deletion_job WHERE subject_id IN "
                     "('user_001', 'user_002')")
    yield
    for subject in ("user_001", "user_002"):
        graph.delete_memories(subject)


# Store a memory and return its id.
def store(fact="Prefers instrumental music", entities=None,
          memory_type="explicit_preference", subject="user_001"):
    r = client.post(
        "/v1/memories",
        json={"subject_id": subject, "memory_type": memory_type, "fact": fact,
              "entities": entities or ["instrumental"], "confidence": 0.9,
              "source_event_ids": [f"evt_{abs(hash(fact)) % 9999}"]},
        headers=AUTH_1 if subject == "user_001" else AUTH_2,
    )
    assert r.status_code == 200, r.json()
    return r.json()["memory_id"]


def patch(memory_id, subject="user_001", headers=None, **body):
    payload = {"subject_id": subject, "operation": "expire",
               "expected_version": 1}
    payload.update(body)
    return client.patch(
        f"/v1/memories/{memory_id}",
        json=payload,
        headers=headers or (AUTH_1 if subject == "user_001" else AUTH_2),
    )


# ==========================================================================
# ENDPOINT 6 - PATCH
# ==========================================================================

# --- Optimistic concurrency (abc.md:313) ---------------------------------

def test_a_stale_version_is_refused():
    """Two people editing at once must not silently overwrite each other."""
    memory_id = store()

    # Somebody else changes it first.
    patch(memory_id, operation="expire", expected_version=1)

    # We still think it is at version 1.
    r = patch(memory_id, operation="expire", expected_version=1)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "CONFLICT"


def test_the_conflict_says_which_version_it_is_at():
    memory_id = store()
    patch(memory_id, operation="expire", expected_version=1)

    message = patch(memory_id, expected_version=1).json()["detail"]["message"]
    assert "version 2" in message


def test_the_right_version_is_accepted():
    memory_id = store()
    assert patch(memory_id, operation="expire", expected_version=1).status_code == 200


# --- Expiring (abc.md:313) -----------------------------------------------

def test_expiring_closes_the_memory_without_deleting_it():
    """abc.md:118 - without erasing audit history prematurely."""
    memory_id = store()
    r = patch(memory_id, operation="expire", expected_version=1)

    assert r.json()["status"] == "expired"
    stored = graph.get_memory(memory_id, "user_001")
    assert stored is not None, "the node must survive"
    assert stored["valid_to"] is not None


def test_an_expired_memory_is_no_longer_active():
    memory_id = store()
    patch(memory_id, operation="expire", expected_version=1)
    assert graph.list_memories("user_001") == []


# --- Correcting (abc.md:313, :118) ---------------------------------------

def test_a_correction_supersedes_rather_than_overwrites():
    memory_id = store("Prefers jazz", ["jazz"])

    r = patch(memory_id, operation="correct", expected_version=1,
              fact="Never liked jazz", entities=["jazz"])

    assert r.status_code == 200
    assert r.json()["superseded"] == memory_id
    assert graph.get_memory(memory_id, "user_001")["status"] == "superseded"


def test_only_the_correction_is_active_afterwards():
    memory_id = store("Prefers jazz", ["jazz"])
    patch(memory_id, operation="correct", expected_version=1,
          fact="Never liked jazz", entities=["jazz"])

    assert [m["fact"] for m in graph.list_memories("user_001")] == ["Never liked jazz"]


def test_a_correction_needs_a_fact():
    memory_id = store()
    r = patch(memory_id, operation="correct", expected_version=1, fact="")
    assert r.status_code == 422


def test_a_sensitive_correction_is_refused():
    """abc.md:53 - no back door here either."""
    memory_id = store()
    r = patch(memory_id, operation="correct", expected_version=1,
              fact="Listens to sad songs because they feel depressed")
    assert r.status_code == 403


# --- Access (abc.md:119) --------------------------------------------------

def test_one_subject_cannot_patch_anothers_memory():
    memory_id = store(subject="user_001")
    r = patch(memory_id, subject="user_001", headers=AUTH_2)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "SUBJECT_MISMATCH"


def test_knowing_a_memory_id_is_not_enough_to_patch_it():
    memory_id = store(subject="user_001")
    r = patch(memory_id, subject="user_002")
    assert r.status_code == 404


def test_patching_needs_a_token():
    r = client.patch("/v1/memories/mem_x",
                     json={"subject_id": "user_001", "operation": "expire",
                           "expected_version": 1})
    assert r.status_code == 401


# ==========================================================================
# ENDPOINT 7 - DELETE
# ==========================================================================

def delete(memory_id, subject="user_001", headers=None):
    return client.delete(
        f"/v1/memories/{memory_id}?subject_id={subject}",
        headers=headers or (AUTH_1 if subject == "user_001" else AUTH_2),
    )


def test_deleting_returns_a_traceable_job_id():
    """abc.md:315 - "return a traceable job identifier"."""
    memory_id = store()
    r = delete(memory_id)

    assert r.status_code == 200
    assert r.json()["job_id"].startswith("job_")
    assert r.json()["memory_id"] == memory_id


def test_the_memory_is_gone_from_the_graph():
    memory_id = store()
    delete(memory_id)
    assert graph.get_memory(memory_id, "user_001") is None


def test_the_memory_stops_being_retrievable():
    """abc.md:97 - "Revokes retrieval eligibility" happens first, before
    any store is actually cleared."""
    store("Prefers instrumental music", ["instrumental"])
    memory_id = graph.list_memories("user_001")[0]["memory_id"]

    delete(memory_id)

    r = client.post("/v1/memories/search",
                    json={"subject_id": "user_001", "intent": "instrumental music"},
                    headers=AUTH_1)
    facts = [m["fact"] for m in r.json()["results"]]
    assert "Prefers instrumental music" not in facts


def test_the_vector_goes_with_the_memory():
    """abc.md:55 - graph and vector share an id "so erasure is complete".

    They are the same node here, so there is no second store to forget.
    """
    memory_id = store("Prefers instrumental music", ["instrumental"])
    delete(memory_id)

    hits = embeddings.search("user_001", "instrumental music", limit=10)
    assert all(h["memory_id"] != memory_id for h in hits)


def test_deleting_something_that_does_not_exist_is_refused():
    r = delete("mem_does_not_exist")
    assert r.status_code == 404


def test_one_subject_cannot_delete_anothers_memory():
    memory_id = store(subject="user_001")
    r = delete(memory_id, subject="user_001", headers=AUTH_2)
    assert r.status_code == 403


def test_knowing_a_memory_id_is_not_enough_to_delete_it():
    memory_id = store(subject="user_001")
    assert delete(memory_id, subject="user_002").status_code == 404
    # And it is still there.
    assert graph.get_memory(memory_id, "user_001") is not None


def test_deleting_needs_a_token():
    r = client.delete("/v1/memories/mem_x?subject_id=user_001")
    assert r.status_code == 401


def test_the_deletion_is_audited():
    """A deletion with no record of having happened is worse than none."""
    memory_id = store()
    delete(memory_id)

    actions = [e["action"] for e in db.get_audit("user_001")]
    assert "memory.deleted" in actions


# ==========================================================================
# ENDPOINT 8 - deletion status
# ==========================================================================

def status(job_id, subject="user_001", headers=None):
    return client.get(
        f"/v1/deletions/{job_id}?subject_id={subject}",
        headers=headers or (AUTH_1 if subject == "user_001" else AUTH_2),
    )


def test_the_job_reports_every_store():
    """abc.md:317 - graph, vector, cache, operational-store, backup."""
    memory_id = store()
    job_id = delete(memory_id).json()["job_id"]

    stores = status(job_id).json()["stores"]
    assert set(stores) == {"graph", "vector", "cache", "operational", "backup"}


def test_a_finished_job_says_completed():
    memory_id = store()
    job_id = delete(memory_id).json()["job_id"]

    body = status(job_id).json()
    assert body["status"] == "completed"
    assert body["completed_at"] is not None


def test_each_store_reports_its_own_outcome():
    """A partial failure must be visible, not hidden behind one flag."""
    memory_id = store()
    job_id = delete(memory_id).json()["job_id"]

    stores = status(job_id).json()["stores"]
    assert stores["graph"] == "deleted"
    assert stores["vector"] == "deleted"
    assert stores["cache"] == "deleted"


def test_backups_are_reported_honestly():
    """abc.md:140 - backups are handled "according to policy". A backup
    cannot be edited in place, so claiming it was deleted would be a lie.
    """
    memory_id = store()
    job_id = delete(memory_id).json()["job_id"]

    assert status(job_id).json()["stores"]["backup"] == deletion.BACKUP_POLICY


def test_the_job_names_the_memory_it_deleted():
    memory_id = store()
    job_id = delete(memory_id).json()["job_id"]
    assert status(job_id).json()["memory_id"] == memory_id


def test_one_subject_cannot_read_anothers_job():
    memory_id = store(subject="user_001")
    job_id = delete(memory_id).json()["job_id"]

    assert status(job_id, subject="user_002").status_code == 404


def test_an_unknown_job_is_not_found():
    assert status("job_nope").status_code == 404


def test_reading_a_job_needs_a_token():
    r = client.get("/v1/deletions/job_x?subject_id=user_001")
    assert r.status_code == 401


# --- The events really go (abc.md:140) ------------------------------------
#
# These exist because the first version of delete_from_operational passed an
# empty list to the DELETE and then returned "deleted" - it cleared nothing
# and reported success. A deletion that lies is worse than one that fails.

def test_the_source_events_are_actually_deleted():
    """abc.md:140 - deletion covers operational metadata, not just the graph."""
    import uuid

    # Capture a real event, then build a memory that came from it.
    r = client.post("/v1/events", json={
        "schema_version": "1.0", "subject_id": "user_001",
        "event_type": "ai_interaction", "surface": "chat", "locale": "en-US",
        "occurred_at": "2026-09-25T10:00:00Z", "consent_state": "granted",
        "source_event_id": "src", "idempotency_key": str(uuid.uuid4()),
        "content": "I prefer instrumental music"}, headers=AUTH_1)
    event_id = r.json()["event_id"]
    assert db.get_event(event_id, "user_001") is not None

    created = client.post("/v1/memories", json={
        "subject_id": "user_001", "memory_type": "explicit_preference",
        "fact": "Prefers instrumental music", "entities": ["instrumental"],
        "confidence": 0.9, "source_event_ids": [event_id]}, headers=AUTH_1)

    job = delete(created.json()["memory_id"]).json()

    assert db.get_event(event_id, "user_001") is None, \
        "the event the memory came from must be gone"
    assert status(job["job_id"]).json()["stores"]["operational"] == "deleted"


def test_a_store_with_nothing_in_it_does_not_claim_a_deletion():
    """Reporting "deleted" when nothing was removed is how a broken
    deletion hides."""
    memory_id = store()          # no source events captured for this one
    job_id = delete(memory_id).json()["job_id"]

    stores = status(job_id).json()["stores"]
    assert stores["graph"] == "deleted"
    assert stores["operational"] == "nothing_to_delete"


def test_deleting_a_memory_leaves_other_events_alone():
    """Only the events this memory came from, not the subject's whole
    history."""
    import uuid

    keep = client.post("/v1/events", json={
        "schema_version": "1.0", "subject_id": "user_001",
        "event_type": "ai_interaction", "surface": "chat", "locale": "en-US",
        "occurred_at": "2026-09-25T10:00:00Z", "consent_state": "granted",
        "source_event_id": "src", "idempotency_key": str(uuid.uuid4()),
        "content": "something unrelated"}, headers=AUTH_1).json()["event_id"]

    memory_id = store()
    delete(memory_id)

    assert db.get_event(keep, "user_001") is not None, \
        "an unrelated event must survive"
