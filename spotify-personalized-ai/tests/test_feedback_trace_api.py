"""Why this file exists
=====================

Endpoint 9 has one requirement that is easy to write and easy to get
wrong: feedback must be recorded "without self-validating model output"
(abc.md:318, :149).

The trap is a loop that flatters the system. It guesses something, shows
it in a reply, the listener clicks thumbs-up on the reply, and the guess
gets more certain - on the strength of its own output. Most of the tests
below exist to prove that cannot happen.

Endpoint 10 has one too: the trace must be useful to a reviewer and
carry no private content (abc.md:320, :322). There are tests for both
halves, because a trace that is safe but useless fails just as badly.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from memory import db, embeddings, graph, trace
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
        trace.delete_traces(subject)
        db.set_consent(subject, "granted")
    with db.connect() as conn:
        conn.execute("DELETE FROM feedback WHERE subject_id IN "
                     "('user_001', 'user_002')")
    yield
    for subject in ("user_001", "user_002"):
        graph.delete_memories(subject)
        trace.delete_traces(subject)


# Store a memory and return its id.
def store(fact="Prefers instrumental music", entities=None,
          memory_type="explicit_preference", subject="user_001"):
    r = client.post(
        "/v1/memories",
        json={"subject_id": subject, "memory_type": memory_type, "fact": fact,
              "entities": entities or ["instrumental"], "confidence": 0.9,
              "source_event_ids": [str(uuid.uuid4())]},
        headers=AUTH_1 if subject == "user_001" else AUTH_2,
    )
    assert r.status_code == 200, r.json()
    return r.json()["memory_id"]


def send_feedback(subject="user_001", headers=None, **body):
    payload = {"subject_id": subject, "kind": "relevance",
               "sentiment": "helpful"}
    payload.update(body)
    return client.post(
        "/v1/feedback", json=payload,
        headers=headers or (AUTH_1 if subject == "user_001" else AUTH_2),
    )


# ==========================================================================
# ENDPOINT 9 - feedback
# ==========================================================================

# --- Not self-validating (abc.md:318, :149) -------------------------------

def test_a_thumbs_up_on_our_own_guess_does_not_count():
    """The heart of the requirement.

    A candidate preference is something WE inferred. Positive feedback on
    it is not evidence for it - the listener is reacting to our output.
    """
    memory_id = store("Might enjoy ambient music", ["ambient"],
                      memory_type="candidate_preference")

    body = send_feedback(memory_id=memory_id, sentiment="helpful").json()

    assert body["recorded"] is True, "it must still be recorded"
    assert body["reinforced"] is False, "but it must not count as evidence"
    assert "inferred by us" in body["reinforce_reason"]


def test_a_thumbs_up_on_an_episode_does_not_count_either():
    """An episode is something we observed, not something they said."""
    memory_id = store("Played a focus playlist", ["instrumental"],
                      memory_type="episode")
    body = send_feedback(memory_id=memory_id, sentiment="helpful").json()
    assert body["reinforced"] is False


def test_a_thumbs_up_on_their_own_words_does_count():
    """An explicit preference came from the listener. Agreeing with it is
    real evidence."""
    memory_id = store("Prefers instrumental music", ["instrumental"],
                      memory_type="explicit_preference")

    body = send_feedback(memory_id=memory_id, sentiment="helpful").json()
    assert body["reinforced"] is True
    assert "own words" in body["reinforce_reason"]


@pytest.mark.parametrize("memory_type", ["explicit_preference", "exclusion",
                                         "correction"])
def test_every_stated_type_can_be_reinforced(memory_type):
    memory_id = store("Does not want country music", ["country"],
                      memory_type=memory_type)
    assert send_feedback(memory_id=memory_id,
                         sentiment="helpful").json()["reinforced"] is True


@pytest.mark.parametrize("memory_type", ["candidate_preference", "episode"])
def test_no_inferred_type_can_be_reinforced(memory_type):
    memory_id = store("Might enjoy ambient", ["ambient"],
                      memory_type=memory_type)
    assert send_feedback(memory_id=memory_id,
                         sentiment="helpful").json()["reinforced"] is False


# --- Negative feedback always counts --------------------------------------

@pytest.mark.parametrize("sentiment", ["unhelpful", "wrong"])
@pytest.mark.parametrize("memory_type", ["explicit_preference",
                                         "candidate_preference", "episode"])
def test_being_told_we_are_wrong_always_counts(sentiment, memory_type):
    """Whatever produced the memory, the listener saying it is wrong is
    information from them."""
    memory_id = store("Something", ["jazz"], memory_type=memory_type)

    body = send_feedback(memory_id=memory_id, kind="rejection",
                         sentiment=sentiment).json()
    assert body["reinforced"] is True
    assert "negative feedback always counts" in body["reinforce_reason"]


def test_negative_feedback_reaches_the_ranking_signal():
    """abc.md:124 lists negative feedback as a rerank signal, so it has to
    be readable by retrieval."""
    memory_id = store()
    send_feedback(memory_id=memory_id, kind="rejection", sentiment="wrong")
    assert memory_id in db.negative_feedback("user_001")


# --- The four kinds (abc.md:318) ------------------------------------------

@pytest.mark.parametrize("kind", ["relevance", "correction", "rejection",
                                  "experience"])
def test_every_kind_of_feedback_is_accepted(kind):
    memory_id = store()
    r = send_feedback(memory_id=memory_id, kind=kind, sentiment="unhelpful")
    assert r.status_code == 200


def test_feedback_about_the_whole_experience_needs_no_memory():
    """Not all feedback is about one memory."""
    r = send_feedback(kind="experience", sentiment="unhelpful")
    assert r.status_code == 200
    assert r.json()["reinforced"] is True


def test_an_unknown_kind_is_refused():
    assert send_feedback(kind="vibes", sentiment="helpful").status_code == 422


# --- Access ---------------------------------------------------------------

def test_feedback_needs_a_token():
    r = client.post("/v1/feedback",
                    json={"subject_id": "user_001", "kind": "relevance",
                          "sentiment": "helpful"})
    assert r.status_code == 401


def test_one_subject_cannot_leave_feedback_for_another():
    r = send_feedback(subject="user_001", headers=AUTH_2)
    assert r.status_code == 403


def test_feedback_cannot_name_another_subjects_memory():
    memory_id = store(subject="user_001")
    r = send_feedback(subject="user_002", memory_id=memory_id)
    assert r.status_code == 404


# ==========================================================================
# ENDPOINT 10 - traces
# ==========================================================================

def search(intent="instrumental music", subject="user_001"):
    return client.post(
        "/v1/memories/search",
        json={"subject_id": subject, "intent": intent},
        headers=AUTH_1 if subject == "user_001" else AUTH_2,
    )


def get_trace(trace_id, subject="user_001", headers=None):
    return client.get(
        f"/v1/traces/{trace_id}?subject_id={subject}",
        headers=headers or (AUTH_1 if subject == "user_001" else AUTH_2),
    )


# --- It is actually useful (abc.md:101) -----------------------------------

def test_a_search_leaves_a_replayable_trace():
    """abc.md:101 - "replays the retrieval trace, shows candidate scores
    and policy decisions"."""
    store()
    trace_id = search().json()["trace_id"]

    body = get_trace(trace_id).json()
    assert body["decisions"], "a search must leave decisions behind"


def test_the_trace_says_which_memories_were_used():
    memory_id = store()
    trace_id = search().json()["trace_id"]

    used = [d["memory_id"] for d in get_trace(trace_id).json()["decisions"]
            if d["decision"] == "included"]
    assert memory_id in used


def test_the_trace_carries_the_scores():
    store()
    trace_id = search().json()["trace_id"]

    included = [d for d in get_trace(trace_id).json()["decisions"]
                if d["decision"] == "included"]
    assert included[0]["score"] is not None


def test_the_trace_says_why_something_was_dropped():
    """A trace that only shows what was used cannot explain an absence."""
    store("Might enjoy ambient music", ["ambient"],
          memory_type="candidate_preference")

    r = client.post("/v1/memories/search",
                    json={"subject_id": "user_001", "intent": "ambient music",
                          "surface": "player"},
                    headers=AUTH_1)
    trace_id = r.json()["trace_id"]

    excluded = [d for d in get_trace(trace_id).json()["decisions"]
                if d["decision"] == "excluded"]
    assert excluded, "the dropped memory must appear in the trace"
    assert "not allowed on player" in excluded[0]["reason"]


def test_the_trace_shows_what_the_services_did():
    """abc.md:345 - the trace shows service decisions, not only retrieval."""
    store()
    trace_id = search().json()["trace_id"]
    assert get_trace(trace_id).json()["actions"]


# --- It is redacted (abc.md:320, :322) ------------------------------------

def test_the_trace_holds_no_memory_text():
    """abc.md:322 - identifiers and outcomes, not raw private content."""
    secret = "Prefers very private music nobody should read"
    store(secret, ["instrumental"])
    trace_id = search().json()["trace_id"]

    assert secret not in get_trace(trace_id).text
    assert "private music" not in get_trace(trace_id).text


def test_the_trace_says_it_is_redacted():
    store()
    trace_id = search().json()["trace_id"]
    assert get_trace(trace_id).json()["redacted"] is True


def test_the_trace_still_names_the_memory():
    """Redacted must not mean useless: a reviewer needs the id to look the
    memory up through a path that checks their authorisation."""
    memory_id = store()
    trace_id = search().json()["trace_id"]

    ids = [d["memory_id"] for d in get_trace(trace_id).json()["decisions"]]
    assert memory_id in ids


# --- Access ---------------------------------------------------------------

def test_one_subject_cannot_read_anothers_trace():
    """abc.md:320 - "authorized" retrieval decisions."""
    store(subject="user_001")
    trace_id = search(subject="user_001").json()["trace_id"]

    assert get_trace(trace_id, subject="user_002").status_code == 404


def test_an_unknown_trace_is_not_found():
    assert get_trace("cid_nope").status_code == 404


def test_reading_a_trace_needs_a_token():
    assert client.get("/v1/traces/cid_x?subject_id=user_001").status_code == 401


# --- The whole loop -------------------------------------------------------

def test_feedback_can_be_tied_back_to_the_trace_that_caused_it():
    """abc.md:194 - the orchestration layer records feedback links."""
    memory_id = store()
    trace_id = search().json()["trace_id"]

    send_feedback(memory_id=memory_id, kind="rejection",
                  sentiment="wrong", trace_id=trace_id)

    with db.connect() as conn:
        row = conn.execute(
            "SELECT trace_id FROM feedback WHERE subject_id = %s "
            "ORDER BY recorded_at DESC LIMIT 1", ("user_001",)
        ).fetchone()
    assert row[0] == trace_id
