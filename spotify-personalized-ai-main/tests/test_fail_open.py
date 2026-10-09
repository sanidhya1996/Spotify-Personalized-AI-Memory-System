"""Why this file exists
=====================

abc.md §5.5 - "Retrieval fails open to a non-personalized response. No
partial or cross-subject context after timeout or auth failure."

Checks memory/fail_open.py: when retrieval breaks, POST /v1/context/compose
still answers 200 with an explicit "no memory" package, and nothing partial
gets through.
"""

from fastapi.testclient import TestClient

from memory import fail_open, retrieval
from memory.api import app
from memory.auth import mint_token

client = TestClient(app)
AUTH_1 = {"Authorization": f"Bearer {mint_token('user_001', 'chat-surface')}"}
BODY = {"subject_id": "user_001", "intent": "play something", "surface": "player",
        "token_budget": 400}


# Stand-in for Neo4j being down.
def broken_search(**kwargs):
    raise ConnectionError("graph store unreachable")


# A store failure becomes a normal no-memory answer, not a 500.
def test_compose_answers_without_memory_when_retrieval_fails(monkeypatch):
    monkeypatch.setattr(retrieval, "search", broken_search)

    r = client.post("/v1/context/compose", json=BODY, headers=AUTH_1)

    assert r.status_code == 200
    package = r.json()
    assert package["no_memory"] is True
    assert package["items"] == []
    assert "memory unavailable" in package["reason"]


# The wrapper returns nothing partial and says what failed.
def test_search_or_nothing_returns_empty_and_the_reason(monkeypatch):
    monkeypatch.setattr(retrieval, "search", broken_search)

    found, failure = fail_open.search_or_nothing(subject_id="user_001", intent="x")

    assert found["results"] == [] and found["removed"] == []
    assert failure == "ConnectionError"


# When retrieval works, the wrapper changes nothing.
def test_search_or_nothing_passes_a_good_result_through(monkeypatch):
    good = {"results": [], "removed": [], "considered": 3}
    monkeypatch.setattr(retrieval, "search", lambda **kwargs: good)

    assert fail_open.search_or_nothing(subject_id="user_001", intent="x") == (good, None)
