"""Why this file exists
=====================

abc.md:309 - "Return ranked subject-scoped memories for intent, surface,
              locale, and token budget."
abc.md:123 - "Use hybrid candidate generation: graph traversal for
              relational relevance and vector similarity for semantic
              relevance."
abc.md:124 - "Rerank by current intent, explicitness, confidence, recency,
              repetition, surface policy, and negative feedback."
abc.md:125 - "Apply diversity and context-budget limits so one preference
              or content cluster does not dominate."

Finding memories is two different problems and they need two different
tools.

Vector search is good at meaning: "music with no vocals" finds "prefers
instrumental". It is poor at precision - it will happily return the
closest thing it has, even when that thing is barely related.

Graph traversal is the opposite: it finds memories attached to an entity
the listener actually named, and misses anything worded differently.

So we do both and merge - which is what "hybrid" means in abc.md:123.

Then the results are reranked, because "closest in meaning" is not the
same as "most worth saying". A thing the listener stated outright last
week beats a guess we made from one play a year ago, even if the guess
scores slightly higher on similarity.

The Quality Engineering Lead put the stakes plainly (abc.md:57): "One
wrong memory can be more damaging than three missing ones."
"""

from datetime import datetime, timezone

from memory import embeddings, entities as entity_resolver, graph, policy
from memory.models import RankedMemory

# How many candidates each half may contribute before reranking.
# abc.md:191 - "Candidate generation is subject-scoped and bounded."
CANDIDATE_LIMIT = 50

# --- The seven signals of abc.md:124 --------------------------------------
#
# The specification names the signals but gives no weights. These numbers
# are OURS. They are gathered here, and nowhere else, so tuning them is one
# edit rather than a hunt through the file.
WEIGHTS = {
    "intent_fit": 0.30,      # how close to what was asked
    "explicitness": 0.25,    # said outright, or guessed
    "confidence": 0.15,      # how sure we were
    "recency": 0.15,         # how recently
    "repetition": 0.10,      # how often said
    "negative_feedback": 0.05,  # marked unhelpful before
}

# How explicit each memory type is. An exclusion and a stated preference
# came from the listener's own words; a candidate preference is our guess.
EXPLICITNESS = {
    "exclusion": 1.0,
    "correction": 1.0,
    "explicit_preference": 1.0,
    "episode": 0.5,
    "candidate_preference": 0.3,
}

# A memory stops counting as recent after this long.
RECENCY_HORIZON_DAYS = 365

# abc.md:125 - no single entity may take over the results.
MAX_PER_ENTITY = 2


# Pull memories attached to the entities named in the query.
def graph_candidates(subject_id: str, query: str) -> list[dict]:
    """The relational half of abc.md:123.

    Finds memories about entities the listener actually named, which
    vector search alone can miss.
    """
    resolved = entity_resolver.resolve_all(query.split())
    entity_ids = [e.entity_id for e in resolved if e.entity_id]
    if not entity_ids:
        return []

    read = """
    MATCH (m:Memory {subject_id: $subject_id, status: 'active'})-[:ABOUT]->(e:Entity)
    WHERE e.entity_id IN $entity_ids
    RETURN DISTINCT m AS memory
    LIMIT $limit
    """
    with graph.driver().session() as session:
        rows = session.run(
            read, subject_id=subject_id, entity_ids=entity_ids, limit=CANDIDATE_LIMIT
        )
        return [dict(r["memory"]) for r in rows]


# Pull memories that mean something similar to the query.
def vector_candidates(subject_id: str, query: str) -> dict[str, float]:
    """The semantic half of abc.md:123. Returns memory_id -> similarity."""
    hits = embeddings.search(subject_id, query, limit=CANDIDATE_LIMIT)
    return {h["memory_id"]: h["score"] for h in hits}


# How recent is this memory, from 1.0 (today) down to 0.0 (a year old).
def recency_score(recorded_at, now: datetime) -> float:
    if recorded_at is None:
        return 0.0
    when = recorded_at.to_native() if hasattr(recorded_at, "to_native") else recorded_at
    age_days = (now - when).total_seconds() / 86400
    return max(0.0, 1.0 - age_days / RECENCY_HORIZON_DAYS)


# How often this has been said, from 1.0 (once) upward, capped.
def repetition_score(evidence_count: int) -> float:
    # Three mentions is as convincing as this signal gets.
    return min(1.0, (evidence_count or 1) / 3)


# Turn a Neo4j temporal value into a plain datetime, or None.
def as_datetime(value):
    """The driver returns its own DateTime type. Pydantic wants a real
    datetime, and a missing value must stay None rather than becoming a
    misleading zero date."""
    if value is None:
        return None
    return value.to_native() if hasattr(value, "to_native") else value


# Combine the seven signals into one score.
def rank_one(memory: dict, similarity: float, now: datetime,
             negative: set[str]) -> RankedMemory:
    signals = {
        "intent_fit": similarity,
        "explicitness": EXPLICITNESS.get(memory.get("memory_type"), 0.5),
        "confidence": float(memory.get("confidence") or 0.0),
        "recency": recency_score(memory.get("recorded_at"), now),
        "repetition": repetition_score(memory.get("evidence_count")),
        # abc.md:124 - negative feedback counts against a memory.
        "negative_feedback": 0.0 if memory["memory_id"] in negative else 1.0,
    }
    score = sum(WEIGHTS[name] * value for name, value in signals.items())

    return RankedMemory(
        memory_id=memory["memory_id"],
        memory_type=memory["memory_type"],
        fact=memory["fact"],
        confidence=float(memory.get("confidence") or 0.0),
        score=round(score, 4),
        signals={k: round(v, 4) for k, v in signals.items()},
        entities=memory.get("entity_ids", []),
        evidence_count=memory.get("evidence_count") or 1,

        # abc.md:340 - the memory explorer needs a timeline and a status.
        # The graph already stores all four (abc.md:117); they were simply
        # not being carried through to the response.
        recorded_at=as_datetime(memory.get("recorded_at")),
        valid_from=as_datetime(memory.get("valid_from")),
        valid_to=as_datetime(memory.get("valid_to")),
        status=memory.get("status") or "active",
    )


# Drop memories this surface is not allowed to show.
def allowed_on_surface(memories: list[RankedMemory], surface: str) -> tuple[list, list]:
    """abc.md:192 - the policy engine removes prohibited items.

    Returns (kept, removed_reasons).
    """
    kept, removed = [], []
    for memory in memories:
        if policy.may_surface(memory.memory_type, surface):
            kept.append(memory)
        else:
            removed.append(f"{memory.memory_id}: {memory.memory_type} not allowed on {surface}")
    return kept, removed


# Stop one entity filling the whole result.
def apply_diversity(memories: list[RankedMemory]) -> tuple[list, list]:
    """abc.md:125 - "so one preference or content cluster does not
    dominate the context pack."

    Ten memories about the same artist is not ten useful memories.
    """
    seen: dict[str, int] = {}
    kept, removed = [], []

    for memory in memories:
        # A memory with no entities cannot crowd anything out.
        crowding = next(
            (e for e in memory.entities if seen.get(e, 0) >= MAX_PER_ENTITY), None
        )
        if crowding:
            removed.append(f"{memory.memory_id}: more than {MAX_PER_ENTITY} about {crowding}")
            continue
        for entity in memory.entities:
            seen[entity] = seen.get(entity, 0) + 1
        kept.append(memory)

    return kept, removed


# Find, rank and filter this subject's memories for one request.
def search(subject_id: str, intent: str, surface: str = "chat",
           limit: int = 10, negative: set[str] | None = None) -> dict:
    now = datetime.now(timezone.utc)
    negative = negative or set()

    # Both halves of abc.md:123, merged on memory id.
    from_graph = {m["memory_id"]: m for m in graph_candidates(subject_id, intent)}
    similarities = vector_candidates(subject_id, intent)

    for memory_id in similarities:
        if memory_id not in from_graph:
            found = graph.get_memory(memory_id, subject_id)
            if found:
                from_graph[memory_id] = found

    # Entity ids are needed for the diversity cap.
    for memory_id, memory in from_graph.items():
        if "entity_ids" not in memory:
            found = graph.get_memory(memory_id, subject_id)
            memory["entity_ids"] = (found or {}).get("entities", [])

    ranked = [
        rank_one(memory, similarities.get(memory_id, 0.0), now, negative)
        for memory_id, memory in from_graph.items()
    ]
    ranked.sort(key=lambda m: m.score, reverse=True)

    ranked, off_policy = allowed_on_surface(ranked, surface)
    ranked, crowded = apply_diversity(ranked)

    return {
        "results": ranked[:limit],
        "removed": off_policy + crowded,
        "considered": len(from_graph),
    }
