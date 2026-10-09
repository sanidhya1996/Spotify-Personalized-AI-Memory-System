"""Why this file exists
=====================

The model proposes; this file decides. abc.md:296 is blunt about it:
"never treat LLM extraction as authoritative without validation", and
abc.md:115 says confidence and policy class come from "deterministic rules
plus structured model output" - rules first, model second.

So everything the model returns passes through here, and anything that
fails a rule is dropped and recorded. The model can be wrong, confused, or
manipulated by text the listener wrote; none of that can reach storage
without getting past this file.

What the rules check (abc.md:341):
  - the memory type is one we actually allow, else drop it
  - confidence is a real number, clamped to 0..1
  - a single action is an episode, never a durable preference
  - nothing sensitive is inferred
  - the fact is not empty and not absurdly long
"""

import re

from memory import dedup, entities as entity_resolver, policy
from memory.models import MEMORY_TYPES, CandidateMemory, ExtractionResult

# Event types that are a single action rather than a statement. One play is
# an episode; it is not evidence of a lasting preference.
#
# abc.md:49 - "Played focus music this morning" is an episode. "Prefers
# low-vocal focus playlists while working" may become a durable preference
# only after explicit confirmation or repeated supporting evidence.
ACTION_EVENTS = ("playback", "save", "follow", "skip")

# The most a single action may claim. Even if the model is certain, one
# play cannot produce a high-confidence memory.
ACTION_CONFIDENCE_CAP = 0.5

# abc.md:53 - "Mood patterns can be sensitive or easily misread. We should
# not store inferred emotional state as a durable profile by default."
# abc.md:136 - "Block or tightly govern sensitive inferred attributes."
#
# A fact matching any of these is dropped rather than stored.
SENSITIVE_PATTERNS = (
    r"\b(depress\w*|anxi\w*|lonely|grief|grieving|suicid\w*|mental health)\b",
    r"\b(pregnan\w*|illness|diagnos\w*|medication|therapy|disabled)\b",
    r"\b(muslim|christian|jewish|hindu|buddhist|atheist|religio\w*)\b",
    r"\b(gay|lesbian|bisexual|transgender|queer|sexual orientation)\b",
    r"\b(democrat|republican|liberal|conservative|political)\b",
    r"\b(race|racial|ethnicity|immigrant|nationality)\b",
)

_SENSITIVE = re.compile("|".join(SENSITIVE_PATTERNS), re.IGNORECASE)


def looks_sensitive(text: str) -> bool:
    """Would storing this amount to inferring a sensitive attribute?

    Deliberately blunt. A false positive costs one forgotten memory; a
    false negative stores something the specification forbids.
    """
    return bool(_SENSITIVE.search(text))


def validate(raw: dict, event: dict) -> tuple[CandidateMemory | None, str | None]:
    """Turn one raw proposal into a memory we are willing to keep.

    Returns (candidate, None) when it passes, or (None, reason) when it
    does not. The reason is returned rather than logged so the caller can
    show what was rejected - abc.md:296 wants rejections visible, not
    silent.
    """
    if not isinstance(raw, dict):
        return None, "not an object"

    # 1. The type must be one we allow. Anything else is dropped, never
    #    guessed at or coerced into the nearest match.
    memory_type = raw.get("memory_type")
    if memory_type not in MEMORY_TYPES:
        return None, f"unknown memory_type: {memory_type!r}"

    # 2. The fact must be real text.
    fact = (raw.get("fact") or "").strip()
    if not fact:
        return None, "empty fact"
    if len(fact) > 500:
        return None, "fact too long"

    # 3. Nothing sensitive, whatever the model decided.
    if looks_sensitive(fact):
        return None, "sensitive inference"

    # 4. Confidence must be a number, and we clamp it ourselves.
    try:
        confidence = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        return None, "confidence is not a number"
    confidence = max(0.0, min(1.0, confidence))

    # 5. A single action cannot become a durable preference.
    if event.get("event_type") in ACTION_EVENTS:
        if memory_type in ("explicit_preference", "exclusion", "correction"):
            return None, f"{memory_type} needs a statement, not a {event['event_type']}"
        confidence = min(confidence, ACTION_CONFIDENCE_CAP)

    # 6. Entities: clean the names, then resolve them to catalog ids
    #    (abc.md:113). Unknown names keep their text but get no id.
    names = [
        str(e).strip()
        for e in (raw.get("entities") or [])
        if isinstance(e, (str, int, float)) and str(e).strip()
    ]
    entities = entity_resolver.resolve_all(names)[:10]

    return (
        CandidateMemory(
            memory_type=memory_type,
            fact=fact,
            entities=entities,
            confidence=round(confidence, 3),
            reason=str(raw.get("reason") or "")[:300],
            # abc.md:115 - the policy class comes from our registry, keyed
            # on the memory type. The model has no say in it.
            policy=policy.classify(memory_type, subject_id=event.get("subject_id")),
            # abc.md:114 - lineage starts with the event it came from.
            source_event_ids=[event["event_id"]] if event.get("event_id") else [],
        ),
        None,
    )


def extract(event: dict, proposals: list[dict]) -> ExtractionResult:
    """Run every proposal through the rules and assemble the answer.

    `proposals` comes from the model. This function never calls a model
    itself, which keeps it fully testable without one.
    """
    kept: list[CandidateMemory] = []
    rejected: list[str] = []

    for raw in proposals:
        candidate, reason = validate(raw, event)
        if candidate is not None:
            kept.append(candidate)
        else:
            rejected.append(reason or "rejected")

    # abc.md:114 - collapse semantically equivalent statements, keeping
    # every source event id. Runs after resolution, because equivalence is
    # judged on resolved entities, not on the words.
    kept = dedup.deduplicate(kept)

    return ExtractionResult(
        event_id=event["event_id"],
        candidates=kept,
        # abc.md:341 - say "no memory" explicitly rather than returning a
        # bare empty list, so a caller can tell nothing-found from not-run.
        no_memory=not kept,
        rejected=rejected,
    )
