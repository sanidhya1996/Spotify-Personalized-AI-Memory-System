"""Why this file exists
=====================

abc.md:114 - "Deduplicate semantically equivalent statements while
retaining source lineage."

A listener says "I like instrumental music" on Monday and "I prefer
instrumental" on Friday. Different words, same meaning. Stored twice, the
system believes it has two pieces of evidence when it has one thing said
twice - and abc.md:49 makes repeated evidence the thing that turns a guess
into a durable preference, so double counting it is not harmless.

Two statements are treated as the same when they are the same memory type
and resolve to the same entities. Comparing resolved entities rather than
words is why entity resolution has to run first: "the weeknd" and
"The Weeknd" only look equal after resolution.

"Retaining source lineage" is the second half of the requirement. Merging
never discards where something came from - the surviving memory keeps
every source event id, so nothing becomes untraceable.
"""

from memory.models import CandidateMemory


# What makes two memories "the same": their type plus their resolved entities.
def signature(candidate: CandidateMemory) -> tuple:
    entities = frozenset(
        e.entity_id or e.name.lower() for e in candidate.entities
    )
    return (candidate.memory_type, entities)


# Fold one memory into another, keeping the better text and all sources.
def merge(kept: CandidateMemory, duplicate: CandidateMemory) -> CandidateMemory:
    # Keep whichever statement the extractor was more sure of.
    if duplicate.confidence > kept.confidence:
        kept = kept.model_copy(
            update={
                "fact": duplicate.fact,
                "confidence": duplicate.confidence,
                "reason": duplicate.reason,
            }
        )

    # abc.md:114 - retain source lineage. Every event that produced this
    # memory stays attached, in order, without repeats.
    sources = list(dict.fromkeys([*kept.source_event_ids, *duplicate.source_event_ids]))

    # abc.md:49 - repeated evidence is what makes a preference durable, so
    # the count is kept rather than thrown away.
    return kept.model_copy(
        update={"source_event_ids": sources, "evidence_count": len(sources)}
    )


# Collapse a list of memories so each distinct thing appears once.
def deduplicate(candidates: list[CandidateMemory]) -> list[CandidateMemory]:
    by_signature: dict[tuple, CandidateMemory] = {}

    for candidate in candidates:
        key = signature(candidate)
        if key in by_signature:
            by_signature[key] = merge(by_signature[key], candidate)
        else:
            by_signature[key] = candidate

    return list(by_signature.values())
