# API 3 — `POST /v1/memories` — Code Flow

Where the request starts, which function runs, what it calls next, where
the data goes, and what comes back.

---

## The path

```
POST /v1/memories
  │
  ├─ STEP 1 ── errors.add_correlation_id()          memory/errors.py
  ├─ STEP 2 ── auth.authenticate()                  memory/auth.py    → 401
  ├─ STEP 3 ── CreateMemoryRequest(...)             memory/models.py  → 422
  │
  └─ STEP 4 ── api.create_memory()                  memory/api.py
       │
       ├─ auth.bind_subject()                       memory/auth.py    → 403
       ├─ cache.is_rate_limited()    ───────────────► REDIS           → 429
       ├─ db.get_consent()           ───────────────► POSTGRES        → 403
       │
       ├─ extraction.looks_sensitive()              memory/extraction.py
       │     the same block as API 2, so this is not a back door → 403
       │
       ├─ entities.resolve_all()                    memory/entities.py
       │     names → catalog ids ───────────────────► data/catalog.yaml
       │
       ├─ policy.classify()                         memory/policy.py
       │     retention + surfaces ──────────────────► data/policy_registry.yaml
       │
       ├─ THEN ONE OF THREE PATHS
       │
       │   (a) the caller named a memory to replace
       │       ├─ graph.get_memory()  ──────────────► NEO4J           → 404
       │       └─ graph.supersede()   ──────────────► NEO4J
       │             closes the old fact, links the new one to it
       │
       │   (b) we look for a clash ourselves
       │       ├─ graph.find_about()  ──────────────► NEO4J
       │       │     memories about exactly these entities
       │       ├─ graph.contradicts()
       │       │     can these two types both be true at once?
       │       │
       │       ├─ IF contradiction → graph.supersede()  ──► NEO4J
       │       ├─ ELIF same thing  → graph.strengthen() ──► NEO4J
       │       │      evidence_count + 1, no second memory
       │       └─ ELSE             → graph.create_memory() ──► NEO4J
       │              a new node, linked to its entities
       │
       ├─ embeddings.store_for_memory()  [background]  memory/embeddings.py
       │     ├─ embeddings.embed()  ────────────────► SentenceTransformers
       │     └─ writes 384 numbers onto the same node ► NEO4J
       │
       └─ db.record_audit()             [background]  memory/db.py
             └───────────────────────────────────────► POSTGRES

RESPONSE
  {"memory_id": "mem_af02dce95af44517",
   "graph_version": 1,
   "policy_state": "normal",
   "superseded": null}
```

---

## Every function, one line each

| Step | Function | File | What it does |
|---|---|---|---|
| 1 | `add_correlation_id()` | `memory/errors.py` | Gives the request a tracking number |
| 2 | `authenticate()` | `memory/auth.py` | Checks the token and reads the subject |
| 3 | *(automatic)* | `memory/models.py` | Requires a valid memory type and a fact |
| 4 | `create_memory()` | `memory/api.py` | The endpoint — checks, resolves, then stores |
| 4.1 | `bind_subject()` | `memory/auth.py` | Refuses another subject's data |
| 4.2 | `is_rate_limited()` | `memory/cache.py` | Counts calls this minute |
| 4.3 | `get_consent()` | `memory/db.py` | Reads our own consent record |
| 4.4 | `looks_sensitive()` | `memory/extraction.py` | Refuses a sensitive fact written directly |
| 4.5 | `resolve_all()` | `memory/entities.py` | Turns written names into catalog ids |
| 4.6 | `classify()` | `memory/policy.py` | Stamps retention, sensitivity, allowed surfaces |
| 4.7 | `find_about()` | `memory/graph.py` | Finds existing memories about exactly these entities |
| 4.8 | `contradicts()` | `memory/graph.py` | Says whether two memory types can both be true |
| 4.9a | `supersede()` | `memory/graph.py` | Closes the old fact and links the new one to it |
| 4.9b | `strengthen()` | `memory/graph.py` | Counts more evidence instead of storing a duplicate |
| 4.9c | `create_memory()` | `memory/graph.py` | Writes a new node and links it to its entities |
| 4.10 | `store_for_memory()` | `memory/embeddings.py` | Writes the 384 numbers onto the same node |
| — | `embed()` | `memory/embeddings.py` | Turns the fact into 384 numbers |
| — | `new_memory_id()` | `memory/graph.py` | Makes the id — never taken from a caller or a model |
| 4.11 | `record_audit()` | `memory/db.py` | Records the outcome |

---

## Where the data goes

| Store | What lands there |
|---|---|
| **Neo4j** | The memory node, its entity links, and its 384 numbers |
| **PostgreSQL** | The audit line; consent is read |
| **Redis** | The rate counter |

---

## The three-way branch is the point

Before storing anything, we ask: **is there already a memory about these
same entities?**

| Found | What happens |
|---|---|
| Something that contradicts it | Close the old one, store the new one, link them |
| The same thing again | Strengthen the existing one — no second memory |
| Nothing | Store a new memory |

That is what stops *"I love country"* and *"no country music"* both being
true at once, and what makes saying something twice count as stronger
evidence rather than two separate facts.

**Nothing is ever deleted.** A superseded memory keeps its node, with
`status: superseded` and a closing date, so the history survives.

---

## What can go wrong, and where

| Response | From |
|---|---|
| 401 `UNAUTHENTICATED` | Step 2 |
| 422 `VALIDATION_FAILED` | Step 3 — unknown memory type, empty fact |
| 403 `SUBJECT_MISMATCH` | Step 4.1 |
| 429 `RATE_LIMITED` | Step 4.2 |
| 403 `CONSENT_DENIED` | Step 4.3, or 4.4 for a sensitive fact |
| 404 `NOT_FOUND` | Path (a) — `supersedes` names no memory of this subject |
| 200 | Any of the three storing paths |
