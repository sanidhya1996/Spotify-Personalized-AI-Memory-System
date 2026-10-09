"""Why this file exists
=====================

abc.md:117 - "Represent memory facts as versioned nodes and relationships
              with valid-from, valid-to, recorded-at, source, confidence,
              and status."
abc.md:118 - "Support contradiction, supersession, expiry, and explicit
              correction without erasing audit history prematurely."
abc.md:119 - "Maintain subject isolation at the query boundary."
abc.md:120 - "Use graph constraints and idempotent upserts."

A memory is not a row that gets overwritten. It is a fact that was true
for a stretch of time. When a listener changes their mind we do not edit
the old memory - we close it and write a new one, linked to it. That is
what "temporal" means here, and it is why the history survives.

Everything in this file is subject-scoped. Every query names the subject,
so no read can cross from one listener to another (abc.md:119).
"""

import os
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from neo4j import GraphDatabase

# Locally the settings come from .env; on a server (Render) they are real
# environment variables and there is no .env. load_dotenv fills in from .env
# only what the environment does not already set, so both work.
load_dotenv()
_driver = None


# Open the connection once and reuse it.
def driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
            auth=(os.environ.get("NEO4J_USER", "neo4j"),
                  os.environ.get("NEO4J_PASSWORD", "")),
        )
    return _driver


# Close the connection, used on shutdown and by tests.
def close() -> None:
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


# Create the uniqueness rules the graph needs. Safe to run repeatedly.
def ensure_constraints() -> None:
    # abc.md:120 - "Use graph constraints ... to protect entity and
    # relationship integrity." Without these, a retry could create two
    # nodes for one memory.
    statements = [
        "CREATE CONSTRAINT memory_id_unique IF NOT EXISTS "
        "FOR (m:Memory) REQUIRE m.memory_id IS UNIQUE",
        "CREATE CONSTRAINT entity_id_unique IF NOT EXISTS "
        "FOR (e:Entity) REQUIRE e.entity_id IS UNIQUE",
        # Reads always filter by subject, so that is what to index.
        "CREATE INDEX memory_subject IF NOT EXISTS "
        "FOR (m:Memory) ON (m.subject_id, m.status)",
    ]
    with driver().session() as session:
        for statement in statements:
            session.run(statement)


# Make a new memory id. Computed by us, never taken from the model.
def new_memory_id() -> str:
    # abc.md:341 - validation rejects "invented memory IDs". Ours are the
    # only ones that exist.
    return f"mem_{uuid.uuid4().hex[:16]}"


# Write one memory into the graph, with its entities and its time window.
def create_memory(subject_id: str, candidate: dict, valid_from: datetime | None = None) -> dict:
    memory_id = new_memory_id()
    now = datetime.now(timezone.utc)
    valid_from = valid_from or now
    policy = candidate.get("policy") or {}

    # MERGE, not CREATE: an idempotent upsert, so replaying the same write
    # cannot produce two nodes (abc.md:120).
    write = """
    MERGE (m:Memory {memory_id: $memory_id})
    ON CREATE SET
        m.subject_id      = $subject_id,
        m.memory_type     = $memory_type,
        m.fact            = $fact,
        m.confidence      = $confidence,
        m.status          = 'active',
        m.graph_version   = 1,
        m.recorded_at     = datetime($recorded_at),
        m.valid_from      = datetime($valid_from),
        m.valid_to        = null,
        m.expires_at      = datetime($expires_at),
        m.sensitivity     = $sensitivity,
        m.source_event_ids = $source_event_ids,
        m.evidence_count  = $evidence_count
    RETURN m.memory_id AS memory_id,
           m.graph_version AS graph_version,
           m.status AS status
    """

    with driver().session() as session:
        record = session.run(
            write,
            memory_id=memory_id,
            subject_id=subject_id,
            memory_type=candidate["memory_type"],
            fact=candidate["fact"],
            confidence=candidate["confidence"],
            recorded_at=now.isoformat(),
            valid_from=valid_from.isoformat(),
            expires_at=policy.get("expires_at") or now.isoformat(),
            sensitivity=policy.get("sensitivity", "normal"),
            source_event_ids=candidate.get("source_event_ids", []),
            evidence_count=candidate.get("evidence_count", 1),
        ).single()

        # Link the memory to what it is about. Entities are shared between
        # memories, so MERGE finds an existing one or makes it.
        for entity in candidate.get("entities", []):
            if not entity.get("entity_id"):
                continue          # unresolved names are not given a node
            session.run(
                """
                MERGE (e:Entity {entity_id: $entity_id})
                ON CREATE SET e.name = $name, e.entity_type = $entity_type
                WITH e
                MATCH (m:Memory {memory_id: $memory_id})
                MERGE (m)-[:ABOUT]->(e)
                """,
                entity_id=entity["entity_id"],
                name=entity.get("canonical_name") or entity["name"],
                entity_type=entity.get("entity_type"),
                memory_id=memory_id,
            )

    return dict(record)


# Read one memory back, but only if it belongs to this subject.
def get_memory(memory_id: str, subject_id: str) -> dict | None:
    # abc.md:119 - subject isolation at the query boundary. The subject is
    # in the MATCH, so knowing an id is not enough to read it.
    read = """
    MATCH (m:Memory {memory_id: $memory_id, subject_id: $subject_id})
    OPTIONAL MATCH (m)-[:ABOUT]->(e:Entity)
    RETURN m AS memory, collect(e.entity_id) AS entities
    """
    with driver().session() as session:
        record = session.run(read, memory_id=memory_id, subject_id=subject_id).single()

    if record is None:
        return None

    memory = dict(record["memory"])
    memory["entities"] = [e for e in record["entities"] if e]
    # Neo4j does not store null properties, so an open memory has no
    # valid_to key at all. Always return it, so callers can just read it.
    memory.setdefault("valid_to", None)
    return memory


# List a subject's active memories, newest first.
def list_memories(subject_id: str, status: str = "active") -> list[dict]:
    read = """
    MATCH (m:Memory {subject_id: $subject_id, status: $status})
    RETURN m AS memory ORDER BY m.recorded_at DESC
    """
    with driver().session() as session:
        return [dict(r["memory"]) for r in session.run(read, subject_id=subject_id, status=status)]


# Replace an old memory with a new one, keeping the old one as history.
def supersede(old_memory_id: str, subject_id: str, new_candidate: dict) -> dict:
    """abc.md:118 - corrections supersede; they never silently overwrite.

    The old memory is closed, not deleted: its valid_to is set and its
    status becomes 'superseded', so the audit history survives.
    """
    now = datetime.now(timezone.utc)
    created = create_memory(subject_id, new_candidate, valid_from=now)

    close_old = """
    MATCH (old:Memory {memory_id: $old_id, subject_id: $subject_id})
    MATCH (new:Memory {memory_id: $new_id})
    SET old.valid_to      = datetime($now),
        old.status        = 'superseded',
        old.graph_version = old.graph_version + 1
    MERGE (new)-[:SUPERSEDES]->(old)
    RETURN old.memory_id AS superseded
    """
    with driver().session() as session:
        session.run(
            close_old,
            old_id=old_memory_id,
            subject_id=subject_id,
            new_id=created["memory_id"],
            now=now.isoformat(),
        ).single()

    created["superseded"] = old_memory_id
    return created


# Remove one subject's memories entirely. Used by tests and by deletion.
def delete_memories(subject_id: str) -> int:
    with driver().session() as session:
        record = session.run(
            "MATCH (m:Memory {subject_id: $subject_id}) "
            "DETACH DELETE m RETURN count(m) AS removed",
            subject_id=subject_id,
        ).single()
    return record["removed"]


# --- Contradiction, evidence and expiry (abc.md:118) ----------------------
#
# abc.md:118 - "Support contradiction, supersession, expiry, and explicit
# correction without erasing audit history prematurely."
#
# Supersession and correction are above. These three complete the set.

# Memory types that cannot both be true about the same thing at once.
#
# The direction matters. Each pair reads (new_type, existing_type): the new
# memory closes the existing one.
OPPOSING = {
    # Both stated by the listener, so either may replace the other. "I said
    # no country, but actually I like it now" is a real change of mind.
    ("explicit_preference", "exclusion"),
    ("exclusion", "explicit_preference"),

    # A stated exclusion closes a guess we had made. Correct direction: the
    # listener telling us no outranks anything we inferred.
    ("exclusion", "candidate_preference"),
}

# Memory types the listener stated outright, as opposed to ones we inferred.
#
# abc.md:49 - a candidate preference "may become a durable preference only
# after explicit confirmation or repeated supporting evidence". abc.md:125
# ranks by explicitness for the same reason.
STATED_TYPES = frozenset({"explicit_preference", "exclusion", "correction"})


# Find this subject's active memories that are about exactly these entities.
def find_about(subject_id: str, entity_ids: list[str]) -> list[dict]:
    # Subject-scoped, like every read (abc.md:119).
    if not entity_ids:
        return []

    read = """
    MATCH (m:Memory {subject_id: $subject_id, status: 'active'})-[:ABOUT]->(e:Entity)
    WITH m, collect(e.entity_id) AS ids
    WHERE apoc.coll.sort(ids) = apoc.coll.sort($entity_ids)
    RETURN m AS memory
    """
    # apoc may not be installed, so do the comparison in Python instead.
    fallback = """
    MATCH (m:Memory {subject_id: $subject_id, status: 'active'})-[:ABOUT]->(e:Entity)
    WITH m, collect(e.entity_id) AS ids
    RETURN m AS memory, ids AS ids
    """
    wanted = set(entity_ids)
    with driver().session() as session:
        rows = session.run(fallback, subject_id=subject_id)
        return [dict(r["memory"]) for r in rows if set(r["ids"]) == wanted]


# Does a new memory contradict an existing one about the same thing?
def contradicts(new_type: str, existing_type: str) -> bool:
    """Does a new memory close an existing one about the same thing?

    A correction always overrides whatever it is about (abc.md:118).

    Otherwise, an INFERRED memory may never close a STATED one. We guessed;
    the listener told us. A guess that could supersede a stated exclusion
    would let the system talk itself out of an instruction it was given -
    and forgetting an exclusion means doing the exact thing the listener
    asked us not to do.

    Found by the golden set: creating a 0.5-confidence candidate preference
    about country music silently superseded a stated exclusion about country
    music, leaving only the guess. The listener would then have been played
    the one thing they had ruled out.
    """
    # A correction always overrides whatever it is about (abc.md:118).
    if new_type == "correction":
        return True

    # We inferred the new one; the listener stated the old one. Not a
    # contradiction to resolve - the stated memory simply stands.
    if new_type not in STATED_TYPES and existing_type in STATED_TYPES:
        return False

    return (new_type, existing_type) in OPPOSING


# Count one more piece of evidence for a memory we already hold.
def strengthen(memory_id: str, subject_id: str, source_event_ids: list[str],
               confidence: float) -> dict:
    """abc.md:49 - repeated evidence is what turns a guess into a durable
    preference. Saying the same thing twice should make us more sure, not
    create a second memory.
    """
    update = """
    MATCH (m:Memory {memory_id: $memory_id, subject_id: $subject_id})
    SET m.source_event_ids = apoc_free_union,
        m.evidence_count   = size(apoc_free_union),
        m.confidence       = CASE WHEN $confidence > m.confidence
                                  THEN $confidence ELSE m.confidence END,
        m.graph_version    = m.graph_version + 1
    RETURN m.memory_id AS memory_id, m.graph_version AS graph_version,
           m.evidence_count AS evidence_count, m.status AS status
    """
    with driver().session() as session:
        existing = session.run(
            "MATCH (m:Memory {memory_id: $memory_id, subject_id: $subject_id}) "
            "RETURN m.source_event_ids AS sources",
            memory_id=memory_id, subject_id=subject_id,
        ).single()
        # Union of sources, in order, without repeats (abc.md:114 lineage).
        merged = list(dict.fromkeys([*(existing["sources"] or []), *source_event_ids]))

        record = session.run(
            update.replace("apoc_free_union", "$sources"),
            memory_id=memory_id, subject_id=subject_id,
            sources=merged, confidence=confidence,
        ).single()
    return dict(record)


# Mark every memory whose retention period has passed as expired.
def expire_memories(subject_id: str | None = None) -> int:
    """abc.md:118 requires expiry; abc.md:133 excludes expired memories.

    Storing expires_at is not enough - something has to act on it. The node
    is not deleted, only marked, so audit history survives (abc.md:118 -
    "without erasing audit history prematurely").
    """
    where_subject = "AND m.subject_id = $subject_id" if subject_id else ""
    update = f"""
    MATCH (m:Memory {{status: 'active'}})
    WHERE m.expires_at < datetime() {where_subject}
    SET m.status = 'expired', m.valid_to = m.expires_at,
        m.graph_version = m.graph_version + 1
    RETURN count(m) AS expired
    """
    with driver().session() as session:
        record = session.run(update, subject_id=subject_id).single()
    return record["expired"]


# Mark one memory expired now, whatever its retention date said.
def expire_one(memory_id: str, subject_id: str) -> dict | None:
    """abc.md:313 - PATCH may "expire" a memory.

    Marked, not deleted: abc.md:118 warns against erasing audit history.
    """
    with driver().session() as session:
        record = session.run(
            """
            MATCH (m:Memory {memory_id: $memory_id, subject_id: $subject_id})
            SET m.status = 'expired', m.valid_to = datetime(),
                m.graph_version = m.graph_version + 1
            RETURN m.memory_id AS memory_id, m.graph_version AS graph_version,
                   m.status AS status
            """,
            memory_id=memory_id, subject_id=subject_id,
        ).single()
    return dict(record) if record else None
