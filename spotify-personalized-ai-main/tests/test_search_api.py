"""Why this file exists
=====================

abc.md:57, the Quality Engineering Lead: "We need precision at the top of
the retrieved set, not just retrieval recall. One wrong memory can be more
damaging than three missing ones."

So these tests check what comes back FIRST and what is kept OUT, not just
that something was returned.
"""

import pytest
from fastapi.testclient import TestClient

from memory import db, embeddings, graph, retrieval
from memory.api import app
from memory.auth import mint_token

client = TestClient(app)
AUTH_1 = {"Authorization": f"Bearer {mint_token('user_001', 'chat-surface')}"}
AUTH_2 = {"Authorization": f"Bearer {mint_token('user_002', 'chat-surface')}"}


@pytest.fixture(autouse=True)
def clean():
    graph.ensure_constraints()
    embeddings.ensure_index()
    for s in ("user_001", "user_002"):
        graph.delete_memories(s)
    yield
    for s in ("user_001", "user_002"):
        graph.delete_memories(s)


# Store a memory the real way, through the API.
def store(fact, entities, memory_type="explicit_preference",
          subject="user_001", confidence=0.9, source=None):
    r = client.post(
        "/v1/memories",
        json={"subject_id": subject, "memory_type": memory_type, "fact": fact,
              "entities": entities, "confidence": confidence,
              "source_event_ids": [source or f"evt_{abs(hash(fact)) % 9999}"]},
        headers=AUTH_1 if subject == "user_001" else AUTH_2,
    )
    assert r.status_code == 200, r.json()
    return r.json()["memory_id"]


# Run a search and return the response.
def search(intent, surface="chat", subject="user_001", limit=10, headers=None):
    return client.post(
        "/v1/memories/search",
        json={"subject_id": subject, "intent": intent,
              "surface": surface, "limit": limit},
        headers=headers or (AUTH_1 if subject == "user_001" else AUTH_2),
    )


# --- Hybrid retrieval (abc.md:123) ----------------------------------------

def test_a_memory_is_found_by_meaning_not_words():
    """The vector half: no shared words between query and memory."""
    store("Prefers instrumental music while working", ["instrumental", "working"])
    store("Enjoys podcasts about true crime", ["true crime"])

    top = search("music with no vocals").json()["results"][0]
    assert top["fact"] == "Prefers instrumental music while working"


def test_a_memory_is_found_by_the_entity_named():
    """The graph half: the listener named the topic outright."""
    store("Does not want country music", ["country"], memory_type="exclusion")
    facts = [m["fact"] for m in search("country").json()["results"]]
    assert "Does not want country music" in facts


def test_both_halves_contribute():
    store("Prefers instrumental music while working", ["instrumental", "working"])
    store("Does not want country music", ["country"], memory_type="exclusion")
    assert search("country songs while working").json()["considered"] >= 2


# --- Reranking, the seven signals (abc.md:124) ----------------------------

def test_every_result_shows_why_it_scored():
    store("Prefers jazz", ["jazz"])
    signals = search("jazz").json()["results"][0]["signals"]
    assert set(signals) == {
        "intent_fit", "explicitness", "confidence",
        "recency", "repetition", "negative_feedback",
    }


def test_a_stated_preference_outranks_a_guess():
    """abc.md:124 - explicitness is a ranking signal.

    Something the listener said beats something we inferred.
    """
    store("Prefers jazz music", ["jazz"], memory_type="explicit_preference")
    store("Might enjoy jazz music", ["jazz"], memory_type="candidate_preference")

    assert search("jazz music").json()["results"][0]["memory_type"] == "explicit_preference"


def test_a_stated_preference_outranks_an_episode():
    store("Prefers jazz music", ["jazz"], memory_type="explicit_preference")
    store("Played jazz music this morning", ["jazz"], memory_type="episode")

    assert search("jazz music").json()["results"][0]["memory_type"] == "explicit_preference"


def test_repetition_raises_the_score():
    """abc.md:49 - repeated evidence is stronger evidence.

    The two mentions must come from DIFFERENT events. The same event
    arriving twice is a retry, not new evidence, so it is deduplicated.
    """
    once = store("Prefers jazz", ["jazz"], source="evt_monday")
    before = search("jazz").json()["results"][0]["score"]

    store("Prefers jazz", ["jazz"], source="evt_friday")
    after = search("jazz").json()["results"][0]

    assert after["score"] > before
    assert after["memory_id"] == once
    assert after["evidence_count"] == 2


def test_the_same_event_twice_is_not_new_evidence():
    """A retry must not look like the listener saying it again."""
    store("Prefers jazz", ["jazz"], source="evt_monday")
    before = search("jazz").json()["results"][0]["score"]

    store("Prefers jazz", ["jazz"], source="evt_monday")   # same event id
    after = search("jazz").json()["results"][0]

    assert after["score"] == before
    assert after["evidence_count"] == 1


def test_the_scores_are_ordered():
    store("Prefers instrumental music", ["instrumental"])
    store("Enjoys true crime podcasts", ["true crime"])
    scores = [m["score"] for m in search("instrumental music").json()["results"]]
    assert scores == sorted(scores, reverse=True)


# --- Surface policy (abc.md:192) ------------------------------------------

def test_a_guess_is_not_shown_on_the_player():
    """A candidate preference is chat-only: an unconfirmed guess should
    not silently steer playback."""
    store("Might enjoy ambient music", ["ambient"], memory_type="candidate_preference")

    on_chat = search("ambient music", surface="chat").json()
    on_player = search("ambient music", surface="player").json()

    assert len(on_chat["results"]) == 1
    assert on_player["results"] == []
    assert "not allowed on player" in on_player["removed"][0]


def test_a_stated_preference_is_shown_everywhere():
    store("Prefers instrumental music", ["instrumental"])
    for surface in ("chat", "player", "search"):
        assert search("instrumental", surface=surface).json()["results"]


def test_removals_say_why():
    store("Played a focus playlist", ["instrumental"], memory_type="episode")
    body = search("focus playlist", surface="player").json()
    assert body["results"] == []
    assert body["removed"], "a removal must be explained"


# --- Diversity (abc.md:125) -----------------------------------------------

def test_one_entity_cannot_fill_the_results():
    """abc.md:125 - one cluster must not dominate the context pack."""
    for n in range(5):
        store(f"Likes jazz for reason {n}", ["jazz"], confidence=0.9 - n * 0.01)

    assert len(search("jazz").json()["results"]) <= retrieval.MAX_PER_ENTITY


def test_different_entities_are_all_kept():
    store("Prefers instrumental music", ["instrumental"])
    store("Enjoys true crime podcasts", ["true crime"])
    store("Does not want country", ["country"], memory_type="exclusion")

    assert len(search("music and podcasts").json()["results"]) == 3


# --- What must never come back --------------------------------------------

def test_a_superseded_memory_is_not_returned():
    """abc.md:133 - exclude contradicted memories."""
    store("Loves country music", ["country"])
    store("Does not want country music", ["country"], memory_type="exclusion")

    facts = [m["fact"] for m in search("country").json()["results"]]
    assert "Loves country music" not in facts


def test_an_expired_memory_is_not_returned():
    """abc.md:133 - exclude expired memories."""
    memory_id = store("Played a focus playlist", ["instrumental"], memory_type="episode")
    with graph.driver().session() as session:
        session.run("MATCH (m:Memory {memory_id: $id}) "
                    "SET m.expires_at = datetime() - duration('P1D')", id=memory_id)
    graph.expire_memories("user_001")

    facts = [m["fact"] for m in search("focus playlist").json()["results"]]
    assert "Played a focus playlist" not in facts


def test_one_subject_never_sees_anothers_memories():
    """abc.md:119 - subject isolation at the query boundary."""
    store("Prefers instrumental music", ["instrumental"], subject="user_001")
    assert search("instrumental music", subject="user_002").json()["results"] == []


def test_a_token_cannot_search_for_another_subject():
    r = search("anything", subject="user_001", headers=AUTH_2)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "SUBJECT_MISMATCH"


# --- The usual guards ------------------------------------------------------

def test_a_token_is_required():
    r = client.post("/v1/memories/search",
                    json={"subject_id": "user_001", "intent": "music"})
    assert r.status_code == 401


def test_consent_is_enforced_before_retrieval():
    """abc.md:53 - enforced before memory reaches retrieval."""
    store("Prefers instrumental music", ["instrumental"])
    db.set_consent("user_001", "paused")

    r = search("instrumental music")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "CONSENT_DENIED"


def test_every_response_links_to_a_trace():
    """abc.md:322 - each response links to a trace."""
    store("Prefers jazz", ["jazz"])
    r = search("jazz")
    assert r.json()["trace_id"] == r.headers["X-Correlation-Id"]


def test_an_empty_history_returns_nothing_not_an_error():
    r = search("anything at all")
    assert r.status_code == 200
    assert r.json()["results"] == []
