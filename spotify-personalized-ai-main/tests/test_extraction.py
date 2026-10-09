"""Why this file exists
=====================

The rules in memory/extraction.py are what stand between a model's guess
and our database (abc.md:296 - "never treat LLM extraction as
authoritative without validation"). They need testing against the cases
that matter, including ones a model would rarely produce on demand.

So these tests call the rules directly with proposals written by hand.
No model is called: the tests are free, instant, and identical every run -
which matters because the free tier allows only five requests a minute.
"""

import pytest

from memory import extraction
from memory.models import MEMORY_TYPES


def event(event_type="ai_interaction", content="some text"):
    return {
        "event_id": "evt_test",
        "subject_id": "user_001",
        "event_type": event_type,
        "surface": "chat",
        "locale": "en-US",
        "content": content,
    }


def proposal(**overrides):
    base = {
        "memory_type": "explicit_preference",
        "fact": "Prefers instrumental music while working",
        "entities": ["instrumental music"],
        "confidence": 0.9,
        "reason": "stated outright",
    }
    base.update(overrides)
    return base


# --- The five types, and only those (abc.md:112) --------------------------

@pytest.mark.parametrize("memory_type", MEMORY_TYPES)
def test_every_allowed_type_is_accepted(memory_type):
    ev = event("ai_interaction")
    candidate, reason = extraction.validate(proposal(memory_type=memory_type), ev)
    assert candidate is not None, reason
    assert candidate.memory_type == memory_type


@pytest.mark.parametrize("bad", ["mood", "preference", "EPISODE", "", None, 7])
def test_unknown_type_is_dropped_not_guessed(bad):
    """A type outside the taxonomy is dropped, never coerced to the nearest."""
    candidate, reason = extraction.validate(proposal(memory_type=bad), event())
    assert candidate is None
    assert "unknown memory_type" in reason


# --- An action is an episode, not a preference (abc.md:49) ----------------

@pytest.mark.parametrize("action", extraction.ACTION_EVENTS)
@pytest.mark.parametrize("durable", ["explicit_preference", "exclusion", "correction"])
def test_one_action_cannot_become_a_durable_preference(action, durable):
    """abc.md:49 - a preference needs a statement or repeated evidence."""
    candidate, reason = extraction.validate(
        proposal(memory_type=durable), event(action)
    )
    assert candidate is None
    assert "needs a statement" in reason


@pytest.mark.parametrize("action", extraction.ACTION_EVENTS)
def test_an_action_may_still_be_an_episode(action):
    candidate, _ = extraction.validate(proposal(memory_type="episode"), event(action))
    assert candidate is not None
    assert candidate.memory_type == "episode"


def test_action_confidence_is_capped():
    """One play is weak evidence however certain the model sounds."""
    candidate, _ = extraction.validate(
        proposal(memory_type="episode", confidence=0.99), event("playback")
    )
    assert candidate.confidence <= extraction.ACTION_CONFIDENCE_CAP


def test_a_statement_keeps_its_confidence():
    candidate, _ = extraction.validate(proposal(confidence=0.95), event("ai_interaction"))
    assert candidate.confidence == 0.95


# --- Sensitive inferences are refused (abc.md:53, :136) -------------------

@pytest.mark.parametrize("fact", [
    "Listens to sad songs because they feel depressed",
    "Is anxious and uses music to cope",
    "Is pregnant and wants calmer playlists",
    "Is a practising muslim",
    "Is gay and likes pop divas",
    "Votes republican",
    "Is an immigrant from Poland",
])
def test_sensitive_inferences_are_dropped(fact):
    """abc.md:53 - do not store inferred emotional or sensitive state."""
    candidate, reason = extraction.validate(proposal(fact=fact), event())
    assert candidate is None
    assert reason == "sensitive inference"


def test_ordinary_music_facts_are_not_treated_as_sensitive():
    candidate, _ = extraction.validate(
        proposal(fact="Prefers low-vocal focus playlists while working"), event()
    )
    assert candidate is not None


# --- Confidence is ours to decide (abc.md:115) ----------------------------

@pytest.mark.parametrize("given,expected", [(1.7, 1.0), (-2.0, 0.0), ("0.4", 0.4)])
def test_confidence_is_clamped_by_us(given, expected):
    candidate, _ = extraction.validate(proposal(confidence=given), event())
    assert candidate.confidence == expected


def test_unparseable_confidence_is_rejected():
    candidate, reason = extraction.validate(proposal(confidence="very sure"), event())
    assert candidate is None
    assert reason == "confidence is not a number"


# --- Facts must be real text ----------------------------------------------

@pytest.mark.parametrize("bad", ["", "   ", None])
def test_empty_facts_are_dropped(bad):
    candidate, reason = extraction.validate(proposal(fact=bad), event())
    assert candidate is None
    assert reason == "empty fact"


def test_absurdly_long_facts_are_dropped():
    candidate, reason = extraction.validate(proposal(fact="x" * 501), event())
    assert candidate is None
    assert reason == "fact too long"


# --- Entities are cleaned, not trusted ------------------------------------

def test_entities_are_deduplicated_and_bounded():
    # Entities are now resolved objects, not strings - see test_entities.py.
    candidate, _ = extraction.validate(
        proposal(entities=["jazz", "jazz", " jazz ", *[f"e{n}" for n in range(20)]]),
        event(),
    )
    jazz = [e for e in candidate.entities if e.entity_id == "topic_jazz"]
    assert len(jazz) == 1
    assert len(candidate.entities) <= 10


def test_non_string_entities_are_discarded():
    candidate, _ = extraction.validate(
        proposal(entities=["jazz", None, {"a": 1}, ["x"]]), event()
    )
    assert [e.name for e in candidate.entities] == ["jazz"]
    assert candidate.entities[0].entity_id == "topic_jazz"


# --- The whole extraction pass --------------------------------------------

def test_good_and_bad_proposals_are_separated():
    proposals = [
        proposal(),                                   # kept
        proposal(memory_type="mood"),                 # dropped
        proposal(fact="Feels depressed on Mondays"),  # dropped
    ]
    result = extraction.extract(event(), proposals)

    assert len(result.candidates) == 1
    assert len(result.rejected) == 2
    assert result.no_memory is False


def test_no_memory_is_explicit_when_nothing_survives():
    """abc.md:341 - say "no memory", do not just return an empty list."""
    result = extraction.extract(event(), [proposal(memory_type="mood")])
    assert result.candidates == []
    assert result.no_memory is True
    assert result.rejected == ["unknown memory_type: 'mood'"]


def test_no_memory_when_the_model_proposes_nothing():
    result = extraction.extract(event(content="hi"), [])
    assert result.no_memory is True
    assert result.rejected == []


def test_rubbish_proposals_do_not_crash_the_pass():
    """The model is untrusted, so its output may not even be objects."""
    result = extraction.extract(event(), ["a string", None, 42, {}])
    assert result.candidates == []
    assert len(result.rejected) == 4


# --- Policy class (abc.md:115, :139, :237, :292) --------------------------

def test_every_candidate_gets_a_policy_class():
    """abc.md:115 - assign confidence AND policy class."""
    candidate, _ = extraction.validate(proposal(), event())
    assert candidate.policy is not None
    assert candidate.policy.sensitivity
    assert candidate.policy.retention_days > 0
    assert candidate.policy.retrieval_eligibility


@pytest.mark.parametrize("memory_type", MEMORY_TYPES)
def test_the_registry_covers_every_memory_type(memory_type):
    """A type with no policy entry could never be stored safely."""
    from memory import policy
    assert policy.classify(memory_type) is not None


def test_policy_comes_from_the_type_not_the_memory():
    """abc.md:139 - retention is applied BY MEMORY TYPE.

    Two different facts of the same type get identical treatment, so no
    per-memory judgement creeps in.
    """
    a, _ = extraction.validate(proposal(fact="Prefers jazz"), event())
    b, _ = extraction.validate(proposal(fact="Prefers techno"), event())
    assert a.policy.retention_days == b.policy.retention_days
    assert a.policy.retrieval_eligibility == b.policy.retrieval_eligibility


def test_an_exclusion_outlives_an_episode():
    """Forgetting an exclusion means doing what the listener forbade."""
    from memory import policy
    assert (policy.classify("exclusion").retention_days
            > policy.classify("episode").retention_days)


def test_an_unconfirmed_guess_is_not_allowed_on_every_surface():
    """abc.md:49 - a candidate preference is not confirmed evidence."""
    from memory import policy
    assert policy.may_surface("explicit_preference", "player") is True
    assert policy.may_surface("candidate_preference", "player") is False


def test_expiry_follows_the_retention_period():
    from datetime import datetime, timezone
    from memory import policy

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    p = policy.classify("episode", now=now)
    assert (p.expires_at - now).days == p.retention_days


def test_the_registry_is_checked_when_it_loads():
    """A malformed registry should fail loudly, not silently misbehave."""
    from memory import policy
    entries = policy.registry()
    for name, entry in entries.items():
        assert set(entry) >= {"sensitivity", "retention_days", "retrieval_eligibility"}
        assert set(entry["retrieval_eligibility"]) <= set(policy.SURFACES)
