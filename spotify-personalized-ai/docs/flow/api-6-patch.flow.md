# API 6 — `PATCH /v1/memories/{memory_id}` — Code Flow

Where the request starts, which function runs, what it calls next, where
the data goes, and what comes back.

> `abc.md:313` — *"Correct, supersede, expire, or change an eligible
> memory **under optimistic concurrency**."*

---

## What it does

Lets a listener take control back: **fix** something the system believes,
or **retire** it.

Two operations:

| Operation | Means |
|---|---|
| `correct` | "That's wrong, here is the right version" |
| `expire` | "Stop using that" |

Neither deletes anything. The old memory is closed and kept.

---

## The path

```
PATCH /v1/memories/{memory_id}
  │
  ├─ STEP 1 ── errors.add_correlation_id()          memory/errors.py
  ├─ STEP 2 ── auth.authenticate()                  memory/auth.py    → 401
  ├─ STEP 3 ── PatchMemoryRequest(...)              memory/models.py  → 422
  │            needs operation and expected_version
  │
  └─ STEP 4 ── api.update_memory()                  memory/api.py
       │
       ├─ auth.bind_subject()                       memory/auth.py    → 403
       ├─ cache.is_rate_limited()    ───────────────► REDIS           → 429
       │
       ├─ graph.get_memory()                        memory/graph.py
       │     read it, subject in the query
       │     └───────────────────────────────────────► NEO4J          → 404
       │
       ├─ THE VERSION CHECK                                           → 409
       │     is graph_version still what the caller last saw?
       │     if not, somebody else changed it — refuse
       │
       ├─ IF operation == "expire"
       │    └─ graph.expire_one()                   memory/graph.py
       │         status → expired, valid_to → now, version + 1
       │         └──────────────────────────────────► NEO4J
       │
       ├─ IF operation == "correct"
       │    ├─ is there a fact to correct it to?                      → 422
       │    │
       │    ├─ extraction.looks_sensitive()         memory/extraction.py
       │    │     the same block as everywhere else                   → 403
       │    │
       │    ├─ entities.resolve_all()               memory/entities.py
       │    │     └──────────────────────────────────► data/catalog.yaml
       │    │
       │    ├─ policy.classify()                    memory/policy.py
       │    │     └──────────────────────────────────► data/policy_registry.yaml
       │    │
       │    ├─ graph.supersede()                    memory/graph.py
       │    │     closes the old one, creates the new one, links them
       │    │     └──────────────────────────────────► NEO4J
       │    │
       │    └─ embeddings.store_for_memory()  [background]
       │         ├─ embeddings.embed()   ───────────► SentenceTransformers
       │         └─                      ───────────► NEO4J
       │
       └─ db.record_audit()             [background]  memory/db.py
             └───────────────────────────────────────► POSTGRES

RESPONSE
  expire  → {"memory_id": "mem_...", "graph_version": 2, "status": "expired"}
  correct → {"memory_id": "mem_NEW", "graph_version": 1,
             "status": "active", "superseded": "mem_OLD"}
```

---

## Every function, one line each

| Step | Function | File | What it does |
|---|---|---|---|
| 1 | `add_correlation_id()` | `memory/errors.py` | Gives the request a tracking number |
| 2 | `authenticate()` | `memory/auth.py` | Checks the token and reads the subject |
| 3 | *(automatic)* | `memory/models.py` | Requires an operation and the version they last saw |
| 4 | `update_memory()` | `memory/api.py` | The endpoint — checks, then corrects or expires |
| 4.1 | `bind_subject()` | `memory/auth.py` | Refuses another subject's data |
| 4.2 | `is_rate_limited()` | `memory/cache.py` | Counts calls this minute |
| 4.3 | `get_memory()` | `memory/graph.py` | Reads the memory, subject-scoped |
| 4.4 | *(inline)* | `memory/api.py` | Refuses if the memory changed since they read it |
| 4.5a | `expire_one()` | `memory/graph.py` | Closes the memory now, without deleting it |
| 4.5b | `looks_sensitive()` | `memory/extraction.py` | Refuses a sensitive correction |
| 4.5b | `resolve_all()` | `memory/entities.py` | Turns names into catalog ids |
| 4.5b | `classify()` | `memory/policy.py` | Stamps retention and surfaces on the correction |
| 4.5b | `supersede()` | `memory/graph.py` | Closes the old fact, links the new one to it |
| 4.6 | `store_for_memory()` | `memory/embeddings.py` | Embeds the corrected fact |
| 4.7 | `record_audit()` | `memory/db.py` | Records the change |

---

## Where the data goes

| Store | What happens |
|---|---|
| **Neo4j** | The memory is read, then closed or superseded; a new node on a correction |
| **PostgreSQL** | The audit line |
| **Redis** | The rate counter |

---

## Optimistic concurrency — the new idea here

The caller sends the version they **last saw**:

```json
{"operation": "expire", "expected_version": 1}
```

If the memory is now at version 2, somebody changed it in between — so we
refuse:

```json
{"code": "CONFLICT",
 "message": "memory is at version 2, not 1"}
```

**Why it matters.** Two support staff open the same memory. One corrects
it; the other expires it a moment later, still looking at the old screen.
Without this check the second write silently undoes the first, and nobody
knows. With it, the second person is told the memory moved and can look
again.

"Optimistic" means we do not lock anything. We let both try, and catch the
clash at the moment of writing.

---

## Nothing is deleted here

| Operation | The old memory |
|---|---|
| `expire` | `status: expired`, `valid_to` set — still readable |
| `correct` | `status: superseded`, linked from the new one — still readable |

`abc.md:118` — *"without erasing audit history prematurely"*. Deleting is
API 7, and even that keeps the audit line.

---

## What can go wrong, and where

| Response | From |
|---|---|
| 401 `UNAUTHENTICATED` | Step 2 |
| 422 `VALIDATION_FAILED` | Step 3, or a correction with no fact |
| 403 `SUBJECT_MISMATCH` | Step 4.1 |
| 429 `RATE_LIMITED` | Step 4.2 |
| 404 `NOT_FOUND` | Step 4.3 — no such memory **for this subject** |
| 409 `CONFLICT` | Step 4.4 — it changed since you read it |
| 403 `CONSENT_DENIED` | A sensitive correction |
| 200 | Expired or corrected |
