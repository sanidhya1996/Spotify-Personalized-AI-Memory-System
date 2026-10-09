"""Why this file exists
=====================

abc.md:122 - "Generate embeddings only for approved memory fields and
              store vectors under the same stable memory identifier used
              in the graph."
abc.md:190 - "Approved semantic fields are embedded and written to a
              vector index under the graph memory identifier. Deletion and
              update events use the same identifier to keep graph and
              vector state aligned."

Searching by words alone fails. "instrumental" would not find "no vocals",
and neither would find "musica sin voces". An embedding turns a sentence
into a list of numbers where similar meanings sit close together, so
search can match on meaning rather than spelling.

The vector is stored ON the memory node itself, so it shares the memory id
by construction. abc.md:55 - "Graph nodes and vector records must share a
stable memory identifier so erasure is complete": deleting the memory
deletes its vector, because they are the same thing. There is no second
store that can drift out of step.

abc.md:212 offers "SentenceTransformers or approved embedding service".
We use the SentenceTransformers model all-MiniLM-L6-v2, run locally: no API
key, no rate limit, and the same answer every time, which keeps tests free
and repeatable.

It runs through fastembed (ONNX) rather than the sentence-transformers
library, because that library brings PyTorch: the API needed about 620 MB
of memory with it and about 230 MB without, and a free server has 512 MB.
Same model, same 384 numbers - checked identical (similarity 1.0) to
sentence-transformers' own output, so stored vectors still match.
"""

from functools import cache

import numpy as np

from memory import graph

# Small, fast, and good enough for short preference sentences. 384 numbers
# per memory.
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
DIMENSIONS = 384

# The index Neo4j uses to search vectors quickly.
#
# The property is `embedding_384`, not `embedding`, on purpose. This Neo4j
# already carries a 64-dimension `memory_embedding_idx` from the previous
# project. A vector index is tied to one dimension count, so 384-number
# vectors cannot share it. Using our own property lets both exist without
# removing anything that was already there.
INDEX_NAME = "memory_embedding_384_idx"
VECTOR_PROPERTY = "embedding_384"

# Only these fields are embedded. abc.md:122 - "only for APPROVED memory
# fields". The fact is the meaning; ids, timestamps and provenance are not.
APPROVED_FIELDS = ("fact",)


# Load the model once. The first call downloads it, then it is cached.
@cache
def model():
    from fastembed import TextEmbedding

    return TextEmbedding(MODEL_NAME)


# Turn one piece of text into a list of numbers.
def embed(text: str) -> list[float]:
    vector = next(iter(model().embed([text])))
    # Length 1, so comparing two vectors is a plain dot product - the same
    # normalization sentence-transformers applied.
    return (vector / np.linalg.norm(vector)).tolist()


# Build the text that gets embedded, from approved fields only.
def approved_text(memory: dict) -> str:
    return " ".join(str(memory.get(field, "")) for field in APPROVED_FIELDS).strip()


# Create the vector index in Neo4j. Safe to run repeatedly.
def ensure_index() -> None:
    statement = f"""
    CREATE VECTOR INDEX {INDEX_NAME} IF NOT EXISTS
    FOR (m:Memory) ON (m.{VECTOR_PROPERTY})
    OPTIONS {{indexConfig: {{
        `vector.dimensions`: {DIMENSIONS},
        `vector.similarity_function`: 'cosine'
    }}}}
    """
    with graph.driver().session() as session:
        session.run(statement)


# Attach a vector to a memory that is already in the graph.
def store_for_memory(memory_id: str, subject_id: str, text: str) -> None:
    # Written onto the memory node, so the vector cannot outlive the
    # memory or belong to a different id (abc.md:55).
    with graph.driver().session() as session:
        session.run(
            f"MATCH (m:Memory {{memory_id: $memory_id, subject_id: $subject_id}}) "
            f"SET m.{VECTOR_PROPERTY} = $vector, m.embedding_model = $model",
            memory_id=memory_id,
            subject_id=subject_id,
            vector=embed(text),
            model=MODEL_NAME,
        )


# Find this subject's memories closest in meaning to a piece of text.
def search(subject_id: str, text: str, limit: int = 10) -> list[dict]:
    """Semantic half of the hybrid retrieval abc.md:123 asks for.

    Subject-scoped like every read: the index is searched, then filtered
    to this subject before anything is returned (abc.md:119).
    """
    limit = min(max(limit, 1), 50)
    query = f"""
    CALL db.index.vector.queryNodes('{INDEX_NAME}', $limit, $vector)
    YIELD node AS m, score
    WHERE m.subject_id = $subject_id AND m.status = 'active'
    RETURN m.memory_id AS memory_id, m.fact AS fact,
           m.memory_type AS memory_type, score
    ORDER BY score DESC
    """
    with graph.driver().session() as session:
        rows = session.run(
            query,
            # Over-fetch, because the index does not know about subjects
            # and the filter runs afterwards.
            limit=limit * 10,
            vector=embed(text),
            subject_id=subject_id,
        )
        return [dict(r) for r in rows][:limit]
