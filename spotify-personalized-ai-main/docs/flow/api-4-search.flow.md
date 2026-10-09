# API 4 — `POST /v1/memories/search` — Code Flow

Where the request starts, which function runs, what it calls next, where
the data goes, and what comes back.

---

## The path

```
POST /v1/memories/search
  │
  ├─ STEP 1 ── errors.add_correlation_id()          memory/errors.py
  ├─ STEP 2 ── auth.authenticate()                  memory/auth.py    → 401
  ├─ STEP 3 ── SearchRequest(...)                   memory/models.py  → 422
  │            needs subject_id and an intent
  │
  └─ STEP 4 ── api.search_memories()                memory/api.py
       │
       ├─ auth.bind_subject()                       memory/auth.py    → 403
       ├─ cache.is_rate_limited()    ───────────────► REDIS           → 429
       ├─ db.get_consent()           ───────────────► POSTGRES        → 403
       │
       ├─ db.negative_feedback()                    memory/db.py
       │     which memories were marked unhelpful
       │     └───────────────────────────────────────► POSTGRES
       │
       └─ retrieval.search()                        memory/retrieval.py
            │  the whole of this endpoint's work
            │
            ├─ A. FIND BY NAME
            │    retrieval.graph_candidates()       memory/retrieval.py
            │      ├─ entities.resolve_all()  ──────► data/catalog.yaml
            │      │     which catalog things did they mention?
            │      └─ Cypher query          ────────► NEO4J
            │            memories linked to those entities
            │
            ├─ B. FIND BY MEANING
            │    retrieval.vector_candidates()      memory/retrieval.py
            │      └─ embeddings.search()           memory/embeddings.py
            │           ├─ embeddings.embed()  ─────► SentenceTransformers
            │           │     turn the question into 384 numbers
            │           └─ vector index query  ─────► NEO4J
            │                 closest memories by meaning
            │
            ├─ C. MERGE A AND B on memory_id
            │    graph.get_memory()            ─────► NEO4J
            │      fills in details for anything only B found
            │
            ├─ D. SCORE EACH ONE
            │    retrieval.rank_one()               memory/retrieval.py
            │      ├─ retrieval.recency_score()     newer counts for more
            │      ├─ retrieval.repetition_score()  said often counts for more
            │      └─ six weighted signals → one score
            │
            ├─ E. SORT, highest score first
            │
            ├─ F. FILTER BY SURFACE
            │    retrieval.allowed_on_surface()     memory/retrieval.py
            │      └─ policy.may_surface()  ────────► data/policy_registry.yaml
            │            is this type allowed on this surface?
            │
            └─ G. FILTER BY DIVERSITY
                 retrieval.apply_diversity()        memory/retrieval.py
                   at most 2 memories about the same entity

       └─ db.record_audit()          [background]   memory/db.py
             └───────────────────────────────────────► POSTGRES

RESPONSE
  {"results": [{"memory_id": "mem_...", "fact": "...",
                "score": 0.831, "signals": {...}}],
   "removed": ["mem_xyz: candidate_preference not allowed on player"],
   "considered": 5,
   "trace_id": "cid_..."}
```

---

## Every function, one line each

| Step | Function | File | What it does |
|---|---|---|---|
| 1 | `add_correlation_id()` | `memory/errors.py` | Gives the request a tracking number |
| 2 | `authenticate()` | `memory/auth.py` | Checks the token and reads the subject |
| 3 | *(automatic)* | `memory/models.py` | Requires a subject and something they asked for |
| 4 | `search_memories()` | `memory/api.py` | The endpoint — checks, then hands off to retrieval |
| 4.1 | `bind_subject()` | `memory/auth.py` | Refuses another subject's data |
| 4.2 | `is_rate_limited()` | `memory/cache.py` | Counts calls this minute |
| 4.3 | `get_consent()` | `memory/db.py` | Consent is enforced before memory is retrieved |
| 4.4 | `negative_feedback()` | `memory/db.py` | Lists memories the listener marked unhelpful |
| 4.5 | `search()` | `memory/retrieval.py` | Runs A–G below and returns the ranked list |
| A | `graph_candidates()` | `memory/retrieval.py` | Finds memories about an entity they named |
| A | `resolve_all()` | `memory/entities.py` | Works out which catalog things the question mentions |
| B | `vector_candidates()` | `memory/retrieval.py` | Finds memories that mean something similar |
| B | `search()` | `memory/embeddings.py` | Searches the vector index, subject-scoped |
| B | `embed()` | `memory/embeddings.py` | Turns the question into 384 numbers |
| C | `get_memory()` | `memory/graph.py` | Fills in details for a vector-only hit |
| D | `rank_one()` | `memory/retrieval.py` | Combines six signals into one score, keeps the breakdown |
| D | `recency_score()` | `memory/retrieval.py` | Newer memories score higher, fading over a year |
| D | `repetition_score()` | `memory/retrieval.py` | Something said three times beats something said once |
| F | `allowed_on_surface()` | `memory/retrieval.py` | Drops what this surface may not show, with the reason |
| F | `may_surface()` | `memory/policy.py` | The surface rule each memory type carries |
| G | `apply_diversity()` | `memory/retrieval.py` | Stops one entity filling the whole result |
| 4.6 | `record_audit()` | `memory/db.py` | Records the outcome |

---

## Where the data goes

| Store | What happens |
|---|---|
| **Neo4j** | **Read twice** — once by entity, once by vector |
| **SentenceTransformers** | The question is turned into 384 numbers |
| **PostgreSQL** | Consent and negative feedback are read; an audit line is written |
| **Redis** | The rate counter |
| **`data/catalog.yaml`** | Read, to spot named entities |
| **`data/policy_registry.yaml`** | Read, for the surface rules |

**Nothing is written** except the audit line. Searching only reads.

---

## The two searches

**Neither alone is enough.**

| | Good at | Bad at |
|---|---|---|
| **By name** (graph) | Precision — they actually said "country" | Missing anything worded differently |
| **By meaning** (vector) | "no vocals" finding "instrumental" | Returning the closest thing even when barely related |

So both run, and the results merge. Something found by both is usually
the strongest answer.

---

## Then order matters more than matching

```
0.831  "Prefers instrumental music while working"    they SAID this
0.690  "Played a focus playlist this morning"        they DID this once
```

The second is a **closer** match to the question. It still ranks lower,
because explicitness outweighs similarity — a thing the listener stated
beats a thing they did once.

---

## What can go wrong, and where

| Response | From |
|---|---|
| 401 `UNAUTHENTICATED` | Step 2 |
| 422 `VALIDATION_FAILED` | Step 3 — no intent given |
| 403 `SUBJECT_MISMATCH` | Step 4.1 |
| 429 `RATE_LIMITED` | Step 4.2 |
| 403 `CONSENT_DENIED` | Step 4.3 |
| 200 with empty results | Nothing found, or everything filtered at F or G |
| 200 with results | The normal path |
