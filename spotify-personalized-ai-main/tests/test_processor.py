"""Why this file exists
=====================

The memory processor is what turns a captured event into a stored memory,
without anyone calling anything. Everything downstream depends on it, and
it runs unattended - so a silent failure here means the system quietly
stops remembering and nobody notices.

abc.md:188 - "A memory processor classifies the event, extracts candidate
facts into a typed schema, resolves canonical content and concept
entities, and applies minimization and sensitivity rules."
abc.md:144 - "Provide dead-letter handling and idempotent replay for
recoverable ingestion failures."

The model is replaced with a stand-in throughout, so these tests are free,
instant, and give the same answer every run.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from memory import db, embeddings, graph, model_client, processor
from memory.api import app
from memory.auth import mint_token

client = TestClient(app)
AUTH_1 = {"Authorization": f"Bearer {mint_token('user_001', 'chat-surface')}"}


# A recorded model answer, of the shape the real one returns.
RECORDED = [
    {
        "memory_type": "explicit_preference",
        "fact": "Prefers instrumental music while working",
        "entities": ["instrumental", "working"],
        "confidence": 0.95,
        "reason": "stated outright",
    }
]


@pytest.fixture(autouse=True)
def clean():
    graph.ensure_constraints()
    embeddings.ensure_index()
    for subject in ("user_001", "user_002"):
        graph.delete_memories(subject)
        db.delete_events(subject)
    db.set_consent("user_001", "granted")
    yield
    for subject in ("user_001", "user_002"):
        graph.delete_memories(subject)
        db.delete_events(subject)


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


# Capture an event the real way, and return its id.
def capture(content="I prefer instrumental music while working",
            subject="user_001", event_type="ai_interaction"):
    r = client.post(
        "/v1/events",
        json={
            "schema_version": "1.0",
            "subject_id": subject,
            "event_type": event_type,
            "surface": "chat",
            "locale": "en-US",
            "occurred_at": "2026-09-25T10:00:00Z",
            "consent_state": "granted",
            "source_event_id": "src_1",
            "idempotency_key": str(uuid.uuid4()),
            "content": content,
        },
        headers=AUTH_1,
    )
    assert r.status_code == 200, r.json()
    return r.json()["event_id"]


# --- The whole job (abc.md:188) -------------------------------------------

def test_an_event_becomes_a_stored_memory(fake_model):
    """The point of the processor: nobody called extract or memories."""
    event_id = capture()
    assert graph.list_memories("user_001") == []

    outcome = processor.process_event(event_id, "user_001")

    assert outcome["stored"] == 1
    stored = graph.list_memories("user_001")
    assert stored[0]["fact"] == "Prefers instrumental music while working"


def test_the_stored_memory_carries_its_source_event(fake_model):
    """abc.md:114 - source lineage survives the whole pipeline."""
    event_id = capture()
    processor.process_event(event_id, "user_001")
    assert event_id in graph.list_memories("user_001")[0]["source_event_ids"]


def test_entities_are_resolved_on_the_way_through(fake_model):
    """abc.md:113 - the worker resolves, exactly as the endpoint does."""
    event_id = capture()
    processor.process_event(event_id, "user_001")

    memory_id = graph.list_memories("user_001")[0]["memory_id"]
    entities = graph.get_memory(memory_id, "user_001")["entities"]
    assert set(entities) == {"topic_instrumental", "activity_working"}


def test_the_memory_is_embedded(fake_model):
    """abc.md:190 - embedding happens when the memory is created, whoever
    creates it."""
    event_id = capture()
    processor.process_event(event_id, "user_001")

    memory_id = graph.list_memories("user_001")[0]["memory_id"]
    with graph.driver().session() as session:
        vector = session.run(
            f"MATCH (m:Memory {{memory_id: $id}}) "
            f"RETURN m.{embeddings.VECTOR_PROPERTY} AS v",
            id=memory_id,
        ).single()["v"]
    assert vector is not None and len(vector) == embeddings.DIMENSIONS


# --- It refuses the same things the endpoints refuse ----------------------

def test_consent_withdrawn_after_capture_stops_the_memory(fake_model):
    """abc.md:53 - consent is enforced before memory is created, and it
    can be withdrawn between capture and processing."""
    event_id = capture()
    db.set_consent("user_001", "paused")

    outcome = processor.process_event(event_id, "user_001")

    assert outcome["stored"] == 0
    assert "consent" in outcome["reason"]
    assert graph.list_memories("user_001") == []


def test_an_event_with_no_words_stores_nothing(fake_model):
    """A skip or a follow has nothing to classify."""
    r = client.post(
        "/v1/events",
        json={"schema_version": "1.0", "subject_id": "user_001",
              "event_type": "skip", "surface": "player", "locale": "en-US",
              "occurred_at": "2026-09-25T10:00:00Z", "consent_state": "granted",
              "source_event_id": "s", "idempotency_key": str(uuid.uuid4())},
        headers=AUTH_1,
    )
    outcome = processor.process_event(r.json()["event_id"], "user_001")
    assert outcome["stored"] == 0
    assert "no content" in outcome["reason"]


def test_the_worker_cannot_read_another_subjects_event(fake_model):
    """The worker has no more privilege than a caller (abc.md:119)."""
    event_id = capture(subject="user_001")
    outcome = processor.process_event(event_id, "user_002")
    assert outcome["stored"] == 0
    assert "not found" in outcome["reason"]


def test_a_sensitive_inference_is_refused():
    """abc.md:53 - the worker applies the same rules as the endpoint."""
    event_id = capture()

    import memory.model_client as mc
    original = mc.propose_candidates
    mc.propose_candidates = lambda event: [{
        "memory_type": "explicit_preference",
        "fact": "Listens to sad songs because they feel depressed",
        "entities": [], "confidence": 0.9, "reason": "x",
    }]
    try:
        outcome = processor.process_event(event_id, "user_001")
    finally:
        mc.propose_candidates = original

    assert outcome["stored"] == 0
    assert graph.list_memories("user_001") == []


# --- Contradiction and evidence, same as the endpoint ---------------------

def test_a_contradiction_supersedes_the_older_memory():
    """A memory written by the worker must behave exactly like one written
    through the API."""
    client.post("/v1/memories", json={
        "subject_id": "user_001", "memory_type": "explicit_preference",
        "fact": "Loves country music", "entities": ["country"],
        "confidence": 0.9, "source_event_ids": ["evt_old"]}, headers=AUTH_1)
    old_id = graph.list_memories("user_001")[0]["memory_id"]

    event_id = capture("no more country music")
    import memory.model_client as mc
    original = mc.propose_candidates
    mc.propose_candidates = lambda event: [{
        "memory_type": "exclusion", "fact": "Does not want country music",
        "entities": ["country"], "confidence": 0.95, "reason": "stated",
    }]
    try:
        processor.process_event(event_id, "user_001")
    finally:
        mc.propose_candidates = original

    assert graph.get_memory(old_id, "user_001")["status"] == "superseded"
    assert [m["fact"] for m in graph.list_memories("user_001")] == [
        "Does not want country music"
    ]


def test_the_same_thing_twice_strengthens_one_memory(fake_model):
    """abc.md:49 - two events saying the same thing is stronger evidence,
    not two memories."""
    first_event = capture()
    processor.process_event(first_event, "user_001")

    second_event = capture()
    processor.process_event(second_event, "user_001")

    memories = graph.list_memories("user_001")
    assert len(memories) == 1
    assert memories[0]["evidence_count"] == 2
    assert set(memories[0]["source_event_ids"]) == {first_event, second_event}


# --- Failure handling (abc.md:144) ----------------------------------------

def test_a_model_outage_raises_so_the_message_can_be_dead_lettered(fake_model):
    """The worker must not swallow a model outage: the event has to be
    retryable, not quietly lost."""
    event_id = capture()
    fake_model(error="503 UNAVAILABLE")

    with pytest.raises(model_client.ModelUnavailable):
        processor.process_event(event_id, "user_001")

    assert graph.list_memories("user_001") == []


def test_nothing_is_half_stored_when_the_model_fails(fake_model):
    event_id = capture()
    fake_model(error="down")
    try:
        processor.process_event(event_id, "user_001")
    except model_client.ModelUnavailable:
        pass
    assert graph.list_memories("user_001") == []


def test_no_memory_is_a_normal_outcome_not_a_failure(fake_model):
    """abc.md:341 - returning nothing is a correct answer."""
    event_id = capture("hi")
    fake_model(proposals=[])

    outcome = processor.process_event(event_id, "user_001")
    assert outcome["stored"] == 0
    assert "no memory" in outcome["reason"]


def test_a_rejected_proposal_is_reported(fake_model):
    event_id = capture()
    fake_model(proposals=[{"memory_type": "mood", "fact": "seems sad",
                           "confidence": 0.9}])

    outcome = processor.process_event(event_id, "user_001")
    assert outcome["stored"] == 0
    assert any("unknown memory_type" in r for r in outcome["rejected"])


# --- The queue itself -----------------------------------------------------

def test_an_accepted_event_is_published_to_the_queue():
    """abc.md:187 - accepted events enter a durable queue."""
    published = []

    import memory.queue as q
    original = q.publish
    q.publish = lambda event_id, subject_id, correlation_id="": published.append(
        (event_id, subject_id)
    )
    try:
        event_id = capture()
    finally:
        q.publish = original

    assert published == [(event_id, "user_001")]


def test_only_identifiers_go_on_the_queue():
    """abc.md:145 - the event content is not copied into a second place."""
    sent = {}

    import memory.queue as q
    original = q.publish
    def spy(event_id, subject_id, correlation_id=""):
        sent.update({"event_id": event_id, "subject_id": subject_id,
                     "correlation_id": correlation_id})
    q.publish = spy
    try:
        capture("something private the listener said")
    finally:
        q.publish = original

    assert set(sent) == {"event_id", "subject_id", "correlation_id"}
    assert "private" not in str(sent)
