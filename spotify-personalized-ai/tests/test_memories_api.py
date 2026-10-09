"""Why this file exists
=====================

POST /v1/memories is the first endpoint that writes something permanent.
Everything after it reads what this stores, so its rules matter more than
most: a memory written for the wrong subject, or one that silently
overwrites a correction, would be wrong forever.

These tests run against the real Neo4j from .env.
"""

import pytest
from fastapi.testclient import TestClient

from memory import db, graph
from memory.api import app
from memory.auth import mint_token

client = TestClient(app)

AUTH_1 = {"Authorization": f"Bearer {mint_token('user_001', 'chat-surface')}"}
AUTH_2 = {"Authorization": f"Bearer {mint_token('user_002', 'chat-surface')}"}


@pytest.fixture(autouse=True)
def clean_graph():
    """Start and finish with no memories for the test subjects."""
    graph.ensure_constraints()
    for subject in ("user_001", "user_002"):
        graph.delete_memories(subject)
    yield
    for subject in ("user_001", "user_002"):
        graph.delete_memories(subject)


# Send a create request, with sensible defaults.
def create(subject="user_001", headers=None, **overrides):
    body = {
        "subject_id": subject,
        "memory_type": "explicit_preference",
        "fact": "Prefers instrumental music while working",
        "entities": ["instrumental", "working"],
        "confidence": 0.95,
        "source_event_ids": ["evt_1"],
    }
    body.update(overrides)
    return client.post(
        "/v1/memories",
        json=body,
        headers=headers or (AUTH_1 if subject == "user_001" else AUTH_2),
    )


# --- What abc.md:306 asks it to return ------------------------------------

def test_it_returns_id_version_and_policy_state():
    r = create()
    assert r.status_code == 200

    body = r.json()
    assert body["memory_id"].startswith("mem_")
    assert body["graph_version"] == 1
    assert body["policy_state"] == "normal"


def test_the_memory_is_actually_in_the_graph():
    memory_id = create().json()["memory_id"]
    stored = graph.get_memory(memory_id, "user_001")

    assert stored["fact"] == "Prefers instrumental music while working"
    assert stored["status"] == "active"


# --- Versioned, with a time window (abc.md:117) ---------------------------

def test_a_new_memory_is_open_ended():
    """valid_from is set, valid_to is not - it is true from now until told
    otherwise."""
    stored = graph.get_memory(create().json()["memory_id"], "user_001")
    assert stored["valid_from"] is not None
    assert stored["valid_to"] is None


def test_provenance_is_stored():
    """abc.md:117 - source and confidence stay attached to the fact."""
    stored = graph.get_memory(
        create(source_event_ids=["evt_a", "evt_b"]).json()["memory_id"], "user_001"
    )
    assert stored["source_event_ids"] == ["evt_a", "evt_b"]
    assert stored["confidence"] == 0.95
    assert stored["recorded_at"] is not None


# --- Entities are resolved by us, not taken as given (abc.md:113) ---------

def test_entities_are_resolved_to_catalog_ids():
    stored = graph.get_memory(
        create(entities=["The Weeknd", "working"]).json()["memory_id"], "user_001"
    )
    assert set(stored["entities"]) == {"artist_the_weeknd", "activity_working"}


def test_two_spellings_reach_the_same_entity():
    a = graph.get_memory(create(entities=["the weeknd"]).json()["memory_id"], "user_001")
    b = graph.get_memory(create(entities=["The Weeknd"]).json()["memory_id"], "user_001")
    assert a["entities"] == b["entities"] == ["artist_the_weeknd"]


def test_unknown_names_get_no_entity_node():
    """A wrong id is worse than none, so unresolved names are not linked."""
    stored = graph.get_memory(
        create(entities=["a band nobody knows"]).json()["memory_id"], "user_001"
    )
    assert stored["entities"] == []


# --- Corrections supersede, never overwrite (abc.md:118) ------------------

def test_a_correction_keeps_the_old_memory_as_history():
    first = create(fact="Prefers jazz").json()["memory_id"]

    second = create(
        memory_type="correction", fact="Never liked jazz", supersedes=first
    ).json()

    old = graph.get_memory(first, "user_001")
    assert old is not None, "history must survive"
    assert old["status"] == "superseded"
    assert old["valid_to"] is not None, "the old fact is closed, not deleted"
    assert second["superseded"] == first


def test_only_the_new_memory_is_active_after_a_correction():
    first = create(fact="Prefers jazz").json()["memory_id"]
    create(memory_type="correction", fact="Never liked jazz", supersedes=first)

    active = [m["fact"] for m in graph.list_memories("user_001")]
    assert active == ["Never liked jazz"]


def test_superseding_a_memory_that_does_not_exist_is_refused():
    r = create(supersedes="mem_nope")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "NOT_FOUND"


def test_one_subject_cannot_supersede_anothers_memory():
    """Even naming a real id, it must belong to the caller's subject."""
    theirs = create(subject="user_001").json()["memory_id"]
    r = create(subject="user_002", supersedes=theirs)
    assert r.status_code == 404


# --- Subject isolation (abc.md:119) ---------------------------------------

def test_a_token_cannot_write_for_another_subject():
    r = create(subject="user_001", headers=AUTH_2)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "SUBJECT_MISMATCH"


def test_one_subjects_memory_is_invisible_to_another():
    memory_id = create(subject="user_001").json()["memory_id"]
    assert graph.get_memory(memory_id, "user_001") is not None
    assert graph.get_memory(memory_id, "user_002") is None


def test_listing_returns_only_your_own_memories():
    create(subject="user_001", fact="Prefers jazz")
    create(subject="user_002", fact="Prefers techno")

    assert [m["fact"] for m in graph.list_memories("user_001")] == ["Prefers jazz"]
    assert [m["fact"] for m in graph.list_memories("user_002")] == ["Prefers techno"]


# --- The same guards as everywhere else -----------------------------------

def test_a_token_is_required():
    r = client.post("/v1/memories", json={"subject_id": "user_001",
                                          "memory_type": "episode", "fact": "x"})
    assert r.status_code == 401


def test_consent_is_checked():
    db.set_consent("user_001", "paused")
    r = create()
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "CONSENT_DENIED"


def test_a_sensitive_fact_cannot_be_written_directly():
    """abc.md:53 - this endpoint must not be a way around the rule."""
    r = create(fact="Listens to sad songs because they feel depressed")
    assert r.status_code == 403


def test_an_unknown_memory_type_is_refused():
    r = create(memory_type="mood")
    assert r.status_code == 422


def test_creation_is_audited():
    memory_id = create().json()["memory_id"]
    entry = db.get_audit("user_001")[0]
    assert entry["action"] == "memory.created"
    assert entry["memory_id"] == memory_id


def test_the_audit_holds_no_memory_text():
    create(fact="Prefers very specific private music")
    assert "private music" not in str(db.get_audit("user_001")[0])


# --- Contradiction (abc.md:118, :189) -------------------------------------

def test_an_exclusion_contradicts_an_earlier_preference():
    """Monday "I love country", Friday "I don't want country".

    abc.md:189 - contradictions close prior facts instead of leaving two
    opposite memories both alive.
    """
    liked = create(memory_type="explicit_preference",
                   fact="Loves country music", entities=["country"]).json()["memory_id"]

    now = create(memory_type="exclusion",
                 fact="Does not want country music", entities=["country"]).json()

    old = graph.get_memory(liked, "user_001")
    assert old["status"] == "superseded"
    assert now["superseded"] == liked
    assert [m["fact"] for m in graph.list_memories("user_001")] == [
        "Does not want country music"
    ]


def test_the_old_fact_is_kept_not_deleted():
    """abc.md:118 - without erasing audit history prematurely."""
    liked = create(memory_type="explicit_preference",
                   fact="Loves country", entities=["country"]).json()["memory_id"]
    create(memory_type="exclusion", fact="No country", entities=["country"])

    old = graph.get_memory(liked, "user_001")
    assert old is not None
    assert old["valid_to"] is not None


def test_a_correction_supersedes_whatever_it_is_about():
    first = create(fact="Prefers jazz", entities=["jazz"]).json()["memory_id"]
    create(memory_type="correction", fact="Never liked jazz", entities=["jazz"])
    assert graph.get_memory(first, "user_001")["status"] == "superseded"


def test_a_guess_never_supersedes_a_stated_exclusion():
    """A candidate preference must not close an exclusion.

    Found by the golden set, not by a test: creating a 0.5-confidence
    candidate preference about country music silently superseded a stated
    exclusion about country music, leaving only the guess. The listener
    would then have been played the one thing they had ruled out.

    abc.md:49 - a candidate preference becomes durable "only after explicit
    confirmation or repeated supporting evidence", so it cannot outrank
    something the listener said outright.
    """
    excluded = create(memory_type="exclusion",
                      fact="Does not want country music",
                      entities=["country"]).json()["memory_id"]

    guess = create(memory_type="candidate_preference",
                   fact="Might enjoy modern country crossover",
                   entities=["country"], confidence=0.5).json()

    # The exclusion is untouched, and the guess did not replace it.
    assert graph.get_memory(excluded, "user_001")["status"] == "active"
    assert guess.get("superseded") is None

    # Both are alive: we keep the guess, we simply do not let it win.
    facts = {m["fact"] for m in graph.list_memories("user_001")}
    assert "Does not want country music" in facts


def test_a_stated_exclusion_does_supersede_a_guess():
    """The other direction still works: the listener telling us no closes
    whatever we had inferred."""
    guess = create(memory_type="candidate_preference",
                   fact="Might enjoy country",
                   entities=["country"], confidence=0.5).json()["memory_id"]

    create(memory_type="exclusion", fact="Does not want country music",
           entities=["country"])

    assert graph.get_memory(guess, "user_001")["status"] == "superseded"


def test_memories_about_different_things_do_not_contradict():
    create(memory_type="explicit_preference", fact="Loves jazz", entities=["jazz"])
    create(memory_type="exclusion", fact="No country", entities=["country"])
    assert len(graph.list_memories("user_001")) == 2


# --- Repeated evidence (abc.md:49) ----------------------------------------

def test_saying_the_same_thing_twice_strengthens_one_memory():
    """abc.md:49 - repeated evidence is what makes a preference durable."""
    first = create(fact="Likes jazz", entities=["jazz"],
                   source_event_ids=["evt_1"]).json()["memory_id"]
    again = create(fact="Likes jazz", entities=["jazz"],
                   source_event_ids=["evt_2"]).json()

    assert again["memory_id"] == first, "should not create a second memory"
    assert len(graph.list_memories("user_001")) == 1

    stored = graph.get_memory(first, "user_001")
    assert stored["evidence_count"] == 2
    assert stored["source_event_ids"] == ["evt_1", "evt_2"]


def test_repeating_raises_the_version():
    first = create(entities=["jazz"], source_event_ids=["evt_1"]).json()
    again = create(entities=["jazz"], source_event_ids=["evt_2"]).json()
    assert again["graph_version"] > first["graph_version"]


def test_a_stronger_statement_raises_the_confidence():
    create(entities=["jazz"], confidence=0.5, source_event_ids=["evt_1"])
    second = create(entities=["jazz"], confidence=0.95,
                    source_event_ids=["evt_2"]).json()
    assert graph.get_memory(second["memory_id"], "user_001")["confidence"] == 0.95


def test_a_weaker_statement_does_not_lower_the_confidence():
    create(entities=["jazz"], confidence=0.95, source_event_ids=["evt_1"])
    second = create(entities=["jazz"], confidence=0.2,
                    source_event_ids=["evt_2"]).json()
    assert graph.get_memory(second["memory_id"], "user_001")["confidence"] == 0.95


# --- Expiry (abc.md:118, :133) --------------------------------------------

def test_an_expired_memory_is_marked_not_deleted():
    memory_id = create(memory_type="episode", fact="Played a focus playlist",
                       entities=["instrumental"]).json()["memory_id"]

    # Age it past its retention period.
    with graph.driver().session() as session:
        session.run(
            "MATCH (m:Memory {memory_id: $id}) "
            "SET m.expires_at = datetime() - duration('P1D')",
            id=memory_id,
        )

    assert graph.expire_memories("user_001") == 1

    stored = graph.get_memory(memory_id, "user_001")
    assert stored is not None, "history must survive"
    assert stored["status"] == "expired"
    assert stored["valid_to"] is not None


def test_an_expired_memory_is_no_longer_active():
    memory_id = create(memory_type="episode", entities=["instrumental"]).json()["memory_id"]
    with graph.driver().session() as session:
        session.run(
            "MATCH (m:Memory {memory_id: $id}) "
            "SET m.expires_at = datetime() - duration('P1D')", id=memory_id)

    graph.expire_memories("user_001")
    assert graph.list_memories("user_001") == []


def test_a_memory_within_its_retention_period_is_untouched():
    create(memory_type="explicit_preference", entities=["jazz"])
    assert graph.expire_memories("user_001") == 0
    assert len(graph.list_memories("user_001")) == 1
