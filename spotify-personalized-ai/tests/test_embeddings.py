"""Why this file exists
=====================

abc.md:122 - embeddings are generated "only for approved memory fields"
and stored "under the same stable memory identifier used in the graph".

Two things must hold: search finds things by meaning rather than spelling,
and one listener's vectors never reach another's search.

The model runs locally, so these tests cost nothing and give the same
answer every time.
"""

import pytest
from fastapi.testclient import TestClient

from memory import db, embeddings, graph
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


# Store a memory through the API, so the embedding is written the real way.
def store(fact, entities, subject="user_001", memory_type="explicit_preference"):
    r = client.post(
        "/v1/memories",
        json={"subject_id": subject, "memory_type": memory_type, "fact": fact,
              "entities": entities, "confidence": 0.9,
              "source_event_ids": [f"evt_{abs(hash(fact)) % 1000}"]},
        headers=AUTH_1 if subject == "user_001" else AUTH_2,
    )
    assert r.status_code == 200, r.json()
    return r.json()["memory_id"]


# --- A vector is written when a memory is created (abc.md:190) ------------

def test_creating_a_memory_writes_a_vector():
    memory_id = store("Prefers instrumental music", ["instrumental"])
    with graph.driver().session() as session:
        record = session.run(
            f"MATCH (m:Memory {{memory_id: $id}}) "
            f"RETURN m.{embeddings.VECTOR_PROPERTY} AS v, m.embedding_model AS model",
            id=memory_id,
        ).single()

    assert record["v"] is not None, "no vector was written"
    assert len(record["v"]) == embeddings.DIMENSIONS
    assert record["model"] == embeddings.MODEL_NAME


def test_the_vector_shares_the_memory_id():
    """abc.md:55 - graph nodes and vector records share a stable id, so
    erasure is complete. Ours are the same node, so it cannot drift."""
    memory_id = store("Prefers jazz", ["jazz"])
    graph.delete_memories("user_001")

    hits = embeddings.search("user_001", "jazz music", limit=5)
    assert all(h["memory_id"] != memory_id for h in hits)


def test_only_approved_fields_are_embedded():
    """abc.md:122 - "only for approved memory fields"."""
    assert embeddings.APPROVED_FIELDS == ("fact",)
    text = embeddings.approved_text(
        {"fact": "Prefers jazz", "subject_id": "user_001", "confidence": 0.9}
    )
    assert text == "Prefers jazz"
    assert "user_001" not in text


# --- Meaning, not spelling -------------------------------------------------

def test_search_matches_meaning_not_words():
    store("Prefers instrumental music while working", ["instrumental", "working"])
    store("Enjoys podcasts about true crime", ["true crime"])

    hits = embeddings.search("user_001", "music with no vocals", limit=1)
    assert hits[0]["fact"] == "Prefers instrumental music while working"


def test_search_works_across_languages():
    store("Does not want country music", ["country"], memory_type="exclusion")
    store("Enjoys podcasts about true crime", ["true crime"])

    hits = embeddings.search("user_001", "quiero country", limit=1)
    assert hits[0]["fact"] == "Does not want country music"


def test_closer_meanings_score_higher():
    store("Enjoys podcasts about true crime", ["true crime"])
    store("Prefers instrumental music while working", ["instrumental"])

    hits = {h["fact"]: h["score"] for h in embeddings.search("user_001", "crime shows", limit=5)}
    assert hits["Enjoys podcasts about true crime"] > hits["Prefers instrumental music while working"]


# --- Subject isolation inside the vector search (abc.md:119) --------------

def test_one_subject_never_sees_anothers_vectors():
    store("Prefers instrumental music", ["instrumental"], subject="user_001")
    assert embeddings.search("user_002", "instrumental music") == []


def test_each_subject_finds_only_their_own():
    store("Prefers instrumental music", ["instrumental"], subject="user_001")
    store("Prefers loud techno", ["reggaeton"], subject="user_002")

    assert [h["fact"] for h in embeddings.search("user_001", "music", limit=5)] == [
        "Prefers instrumental music"
    ]
    assert [h["fact"] for h in embeddings.search("user_002", "music", limit=5)] == [
        "Prefers loud techno"
    ]


def test_superseded_memories_drop_out_of_search():
    """abc.md:133 - excluded memories must not be retrievable."""
    store("Loves country music", ["country"])
    store("Does not want country music", ["country"], memory_type="exclusion")

    facts = [h["fact"] for h in embeddings.search("user_001", "country", limit=5)]
    assert "Loves country music" not in facts
