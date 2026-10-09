"""Why this file exists
=====================

Entity resolution (abc.md:113) decides whether two memories are about the
same thing. Get it wrong in one direction and the same artist becomes
several unrelated memories; wrong in the other and a memory is attached to
an artist the listener never mentioned.

Deduplication (abc.md:114) then relies on it, so both are tested here.
"""

import pytest

from memory import dedup, entities, policy
from memory.models import CandidateMemory


# --- Same thing, written differently (abc.md:113) -------------------------

@pytest.mark.parametrize("written", [
    "the weeknd", "The Weeknd", "THE WEEKND", "  The Weeknd  ",
    "weeknd", "Abel Tesfaye",
])
def test_every_spelling_reaches_the_same_artist(written):
    """Three spellings must not become three unrelated memories."""
    assert entities.resolve(written).entity_id == "artist_the_weeknd"


@pytest.mark.parametrize("written,expected", [
    ("instrumental", "topic_instrumental"),
    ("instrumental music", "topic_instrumental"),
    ("no vocals", "topic_instrumental"),
    ("low-vocal", "topic_instrumental"),
    ("country", "topic_country"),
    ("working", "activity_working"),
    ("while working", "activity_working"),
    ("for studying", "activity_studying"),
    ("focus mix", "playlist_deep_focus"),
])
def test_aliases_reach_the_right_entity(written, expected):
    """abc.md:295 - the alias table is what makes this work."""
    assert entities.resolve(written).entity_id == expected


def test_accents_and_punctuation_do_not_matter():
    assert entities.normalise("Beyoncé!") == entities.normalise("beyonce")


# --- Unknown names are left alone, never guessed --------------------------

@pytest.mark.parametrize("unknown", [
    "some band nobody has heard of", "zzzzz", "my own mixtape",
])
def test_unknown_names_stay_unresolved(unknown):
    """A wrong id attaches the memory to the wrong artist. No id is safer."""
    resolved = entities.resolve(unknown)
    assert resolved.entity_id is None
    assert resolved.name == unknown          # the words are kept
    assert resolved.match_confidence == 0.0


def test_a_weak_match_is_not_accepted():
    """abc.md:295 - a confidence threshold for ambiguous concepts."""
    assert entities.MATCH_THRESHOLD >= 0.8
    assert entities.resolve("cou").entity_id is None


def test_resolution_records_how_sure_it_was():
    assert entities.resolve("country music").match_confidence == 1.0


# --- Resolving a list ------------------------------------------------------

def test_two_spellings_collapse_into_one_entity():
    resolved = entities.resolve_all(["the weeknd", "The Weeknd", "weeknd"])
    assert len(resolved) == 1
    assert resolved[0].entity_id == "artist_the_weeknd"


def test_different_entities_are_all_kept():
    resolved = entities.resolve_all(["instrumental", "working", "the weeknd"])
    assert {e.entity_id for e in resolved} == {
        "topic_instrumental", "activity_working", "artist_the_weeknd",
    }


def test_two_unknown_names_are_not_merged():
    resolved = entities.resolve_all(["unknown band a", "unknown band b"])
    assert len(resolved) == 2


# --- Deduplication (abc.md:114) -------------------------------------------

def candidate(fact, entity_names, memory_type="explicit_preference",
              confidence=0.9, source="evt_1"):
    return CandidateMemory(
        memory_type=memory_type,
        fact=fact,
        entities=entities.resolve_all(entity_names),
        confidence=confidence,
        policy=policy.classify(memory_type),
        source_event_ids=[source],
    )


def test_the_same_statement_said_twice_is_stored_once():
    """"I like instrumental" and "I prefer instrumental" are one fact."""
    merged = dedup.deduplicate([
        candidate("Likes instrumental music", ["instrumental"], source="evt_1"),
        candidate("Prefers instrumental", ["instrumental"], source="evt_2"),
    ])
    assert len(merged) == 1


def test_merging_keeps_every_source_event():
    """abc.md:114 - "while retaining source lineage"."""
    merged = dedup.deduplicate([
        candidate("Likes instrumental music", ["instrumental"], source="evt_1"),
        candidate("Prefers instrumental", ["instrumental"], source="evt_2"),
    ])
    assert merged[0].source_event_ids == ["evt_1", "evt_2"]


def test_merging_counts_the_evidence():
    """abc.md:49 - repeated evidence is what makes a preference durable."""
    merged = dedup.deduplicate([
        candidate("Likes jazz", ["jazz"], source="evt_1"),
        candidate("Enjoys jazz", ["jazz"], source="evt_2"),
        candidate("Jazz is good", ["jazz"], source="evt_3"),
    ])
    assert merged[0].evidence_count == 3


def test_the_more_confident_wording_survives():
    merged = dedup.deduplicate([
        candidate("Maybe likes jazz", ["jazz"], confidence=0.4, source="evt_1"),
        candidate("Prefers jazz", ["jazz"], confidence=0.95, source="evt_2"),
    ])
    assert merged[0].fact == "Prefers jazz"
    assert merged[0].confidence == 0.95


def test_different_entities_are_not_merged():
    merged = dedup.deduplicate([
        candidate("Likes jazz", ["jazz"]),
        candidate("Likes country", ["country"]),
    ])
    assert len(merged) == 2


def test_a_preference_and_an_exclusion_are_never_merged():
    """Same subject, opposite meaning. Merging these would be a real bug."""
    merged = dedup.deduplicate([
        candidate("Likes country", ["country"], memory_type="explicit_preference"),
        candidate("Does not want country", ["country"], memory_type="exclusion"),
    ])
    assert len(merged) == 2


def test_dedup_matches_on_resolved_ids_not_on_words():
    """This is why resolution has to run first."""
    merged = dedup.deduplicate([
        candidate("Likes the weeknd", ["the weeknd"], source="evt_1"),
        candidate("Enjoys The Weeknd", ["The Weeknd"], source="evt_2"),
    ])
    assert len(merged) == 1
    assert merged[0].evidence_count == 2


def test_an_unmergeable_memory_keeps_its_single_source():
    merged = dedup.deduplicate([candidate("Likes jazz", ["jazz"], source="evt_9")])
    assert merged[0].source_event_ids == ["evt_9"]
    assert merged[0].evidence_count == 1
