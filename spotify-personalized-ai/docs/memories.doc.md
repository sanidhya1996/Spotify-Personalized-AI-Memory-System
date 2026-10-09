# `POST /v1/memories`

Endpoint 3 of 10. Saves a memory so it survives.

> `abc.md:306` — *"Create an explicit or approved memory and return stable
> ID, graph version, and policy state."*

---

## 1. What it is for

APIs 1 and 2 work things out and then forget them. This is the first
endpoint that writes something permanent.

Before it, the system could tell you *"she doesn't want country music"* and
then lose that the moment the request ended. After it, that fact is still
there next week.

Everything later reads what this stores, so its rules matter more than
most: a memory saved for the wrong subject, or one that quietly overwrote
a correction, would be wrong forever.

---

## 2. The request

```
POST /v1/memories
Authorization: Bearer <token>
```

```json
{
  "subject_id": "user_001",
  "memory_type": "explicit_preference",
  "fact": "Prefers instrumental music while working",
  "entities": ["instrumental", "working"],
  "confidence": 0.95,
  "source_event_ids": ["evt_86e00164b3a8"],
  "supersedes": null
}
```

`entities` are names as written. We resolve them ourselves. `supersedes`
names a memory this one replaces, when the caller already knows.

---

## 3. The response

```json
{
  "memory_id": "mem_af02dce95af44517",
  "graph_version": 1,
  "policy_state": "normal",
  "superseded": null
}
```

Exactly the three things `abc.md:306` asks for, plus what was closed if
anything was.

**Errors**

| Status | Code | When |
|---|---|---|
| 401 | `UNAUTHENTICATED` | Token missing, forged, or expired |
| 403 | `SUBJECT_MISMATCH` | Token belongs to a different subject |
| 403 | `CONSENT_DENIED` | Consent withdrawn, or the fact is sensitive |
| 404 | `NOT_FOUND` | `supersedes` names no memory of this subject |
| 422 | `VALIDATION_FAILED` | Unknown memory type, empty fact |
| 429 | `RATE_LIMITED` | More than 120 requests this minute |

---

## 4. What a stored memory looks like

In Neo4j, as a node:

```
(:Memory {
    memory_id:        "mem_af02dce95af44517",
    subject_id:       "user_001",
    memory_type:      "explicit_preference",
    fact:             "Prefers instrumental music while working",
    confidence:       0.9,
    status:           "active",
    graph_version:    1,
    recorded_at:      2026-09-24T09:07:04Z,     when we learned it
    valid_from:       2026-09-24T09:07:04Z,     when it started being true
    valid_to:         null,                     still true
    expires_at:       2027-09-24T09:07:04Z,     retention, from the registry
    source_event_ids: ["evt_1"],                where it came from
    evidence_count:   1,
    embedding_384:    [0.058, -0.017, ...],     384 numbers
    embedding_model:  "all-MiniLM-L6-v2"
})
```

joined to what it is about:

```
(:Memory) -[:ABOUT]-> (:Entity {entity_id: "topic_instrumental"})
(:Memory) -[:ABOUT]-> (:Entity {entity_id: "activity_working"})
```

`abc.md:117` asks for *"valid-from, valid-to, recorded-at, source,
confidence, and status"* — all present.

---

## 5. Why it is a graph and not a table

Because a memory is not a row that gets overwritten. It is a fact that was
true for a stretch of time.

When a listener changes their mind, the old fact does not disappear. It is
**closed**: `valid_to` is set, `status` becomes `superseded`, and a
`SUPERSEDES` link points from the new fact to the old one.

```
"Prefers jazz"        status: superseded   valid_to: 2026-09-24
        ↑ SUPERSEDES
"Never liked jazz"    status: active       valid_to: null
```

Only the new fact is used. The old one is still readable, so
*"why did the system think I liked jazz?"* always has an answer.

`abc.md:118` — *"without erasing audit history prematurely."*

---

## 6. The four things `abc.md:118` requires

> *"Support **contradiction**, **supersession**, **expiry**, and
> **explicit correction** without erasing audit history prematurely."*

### Supersession

The caller passes `supersedes: "mem_123"`. The old memory is closed, the
new one takes over.

### Explicit correction

A memory with `memory_type: "correction"` supersedes whatever it is about,
even without being told which memory. A correction always wins.

### Contradiction — found by us, not announced

Nobody tells the system that Friday contradicts Monday. It works it out.

```
Monday : explicit_preference  "Loves country music"     about topic_country
Friday : exclusion            "Does not want country"   about topic_country
```

An `exclusion` and a `preference` about the same entities cannot both be
true, so the older one is closed automatically.

`abc.md:189` — *"Contradictions close or supersede prior facts instead of
silently replacing history."*

### Expiry

Every memory carries `expires_at`, from the policy registry. Writing the
date does nothing on its own, so `scripts/expire_memories.py` acts on it:

```
python scripts/expire_memories.py
marked 3 memory(ies) as expired
```

Marked, not deleted — the history survives. `abc.md:133` then excludes
expired memories from retrieval.

---

## 7. Saying the same thing twice

```
Monday : "I like jazz"   evt_1
Friday : "I like jazz"   evt_2
```

This does **not** create a second memory. It strengthens the first:

```
evidence_count:   2
source_event_ids: ["evt_1", "evt_2"]
graph_version:    2
```

`abc.md:49` makes repeated evidence the thing that turns a guess into a
durable preference, so counting it matters. Storing two copies would make
the system look twice as certain as it is.

A more confident statement raises the confidence; a weaker one never
lowers it.

---

## 8. Embeddings

> `abc.md:190` step 10 — *"Approved semantic fields are embedded and
> written to a vector index **under the graph memory identifier**."*

When a memory is saved, its text is turned into 384 numbers. Sentences
that mean similar things get similar numbers, so search can match meaning
rather than spelling:

| Searched for | Found | Score |
|---|---|---|
| "music with no vocals" | "Prefers **instrumental** music while working" | 0.756 |
| "crime shows" | "Enjoys podcasts about **true crime**" | 0.834 |
| "quiero country" *(Spanish)* | "Does not want **country** music" | 0.659 |

Not one shared word in any of them.

**The vector lives on the memory node itself**, as the `embedding_384`
property. `abc.md:55` requires graph and vector to share a stable id *"so
erasure is complete"* — ours are the same node, so they cannot drift apart
and deleting the memory deletes the vector.

Only the `fact` is embedded. `abc.md:122` — *"only for approved memory
fields"*; ids, timestamps and provenance are not meaning.

The model is `all-MiniLM-L6-v2` via SentenceTransformers (`abc.md:212`
offers *"SentenceTransformers or approved embedding service"*). It runs
locally: no API key, no rate limit, same answer every time, so tests are
free and repeatable.

> **Note:** the vector property is `embedding_384`, not `embedding`. This
> Neo4j already held a 64-dimension index from the previous project, and a
> vector index is tied to one dimension count. Using our own property lets
> both exist without removing anything.

---

## 9. Subject isolation

`abc.md:119` — *"Maintain subject isolation at the query boundary and
prevent traversal across unauthorized identity scopes."*

Every query names the subject. Not a filter applied afterwards — it is in
the `MATCH` itself:

```cypher
MATCH (m:Memory {memory_id: $memory_id, subject_id: $subject_id})
```

So knowing a memory id is **not enough** to read it, list it, or supersede
it. Four tests cover this, including the vector search, where the index is
searched and then filtered to the subject before anything is returned.

---

## 10. Things the caller cannot decide

| Decided by us | Why |
|---|---|
| `memory_id` | `abc.md:341` rejects invented memory ids |
| Entity ids | Resolved through our catalog, not taken as given |
| Policy class | From the registry, keyed on memory type |
| `expires_at` | Follows the policy class |
| The embedding | Computed from the fact |

And a **sensitive fact cannot be written through this endpoint** — the
same check as extraction. It must not be a way around `abc.md:53`.

---

## 10b. Call flow — what actually runs, in order

```
POST /v1/memories
  │
  ├─1 errors.add_correlation_id()          memory/errors.py
  ├─2 auth.authenticate()                  memory/auth.py        -> 401
  ├─3 CreateMemoryRequest validation       memory/models.py      -> 422
  │
  └─4 api.create_memory()                  memory/api.py
        │
        ├─ auth.bind_subject()             memory/auth.py        -> 403
        ├─ cache.is_rate_limited()  ──────────────► REDIS        -> 429
        ├─ db.get_consent()         ──────────────► POSTGRES     -> 403
        │
        ├─ extraction.looks_sensitive()    memory/extraction.py
        │    no back door around abc.md:53                       -> 403
        │
        ├─ entities.resolve_all()          memory/entities.py
        │    names to catalog ids   ──────────────► data/catalog.yaml
        │
        ├─ policy.classify()               memory/policy.py
        │    retention + surfaces   ──────────────► data/policy_registry.yaml
        │
        ├─ IF the caller named a memory to replace:
        │    ├─ graph.get_memory()   ──────────────► NEO4J       -> 404
        │    └─ graph.supersede()    ──────────────► NEO4J
        │
        ├─ ELSE look for a clash ourselves:
        │    ├─ graph.find_about()         memory/graph.py
        │    │     memories about exactly these entities
        │    │                      ──────────────► NEO4J
        │    │
        │    ├─ graph.contradicts()        memory/graph.py
        │    │     can these two types both be true?
        │    │
        │    ├─ IF contradiction  -> graph.supersede()   ──► NEO4J
        │    │      closes the old fact, links the new one to it
        │    │
        │    ├─ ELIF same thing   -> graph.strengthen()  ──► NEO4J
        │    │      evidence_count + 1, no second memory
        │    │
        │    └─ ELSE             -> graph.create_memory() ──► NEO4J
        │           new node, linked to its entities
        │
        ├─ [background] embeddings.store_for_memory()  memory/embeddings.py
        │     │  embeds the fact and writes it onto the same node
        │     ├─ embeddings.embed()   ──────────────► SentenceTransformers
        │     └─                      ──────────────► NEO4J
        │
        └─ [background] db.record_audit()  ──────────► POSTGRES

  response: {"memory_id": "mem_...", "graph_version": 1,
             "policy_state": "normal", "superseded": null}
```

**The three-way branch is the heart of this endpoint.** A new memory is
either a contradiction (close the old one), a repeat (strengthen the
existing one), or genuinely new.

**The embedding is written onto the same node**, so graph and vector share
an id by construction rather than by bookkeeping.

---

## 11. Files

| File | Job |
|---|---|
| `memory/graph.py` | Neo4j: store, read, supersede, strengthen, expire |
| `memory/embeddings.py` | Make the vectors, search by them |
| `memory/api.py` | The endpoint |
| `scripts/expire_memories.py` | Act on `expires_at` |
| `tests/test_memories_api.py` | 31 tests |
| `tests/test_embeddings.py` | 9 tests |

---

## 11b. The functions this API uses

Names and why, not code.

**`memory/api.py`**

| Function | Why it exists |
|---|---|
| `create_memory()` | The endpoint: resolves, stamps policy, looks for a clash, then stores |

**`memory/graph.py`** - Neo4j

| Function | Why it exists |
|---|---|
| `driver()` | Opens the connection once and reuses it |
| `ensure_constraints()` | Uniqueness rules, so a retry cannot create two nodes for one memory |
| `new_memory_id()` | Ids are computed by us; the model is never allowed to invent one |
| `create_memory()` | Writes the memory node and links it to its entities |
| `get_memory()` | Reads one back - with the subject in the query, so an id alone is not enough |
| `list_memories()` | A subject's active memories, newest first |
| `supersede()` | Closes an old fact and links the new one to it, instead of overwriting |
| `find_about()` | Finds existing memories about exactly these entities - the contradiction lookup |
| `contradicts()` | Decides whether two memory types can both be true at once |
| `strengthen()` | Counts one more piece of evidence instead of storing a duplicate |
| `expire_memories()` | Marks memories past their retention date, without deleting them |
| `delete_memories()` | Removes a subject's memories; used by deletion and by tests |

**`memory/embeddings.py`**

| Function | Why it exists |
|---|---|
| `model()` | Loads the embedding model once, on first use |
| `embed()` | Turns a sentence into 384 numbers |
| `approved_text()` | Picks only the fields allowed to be embedded - the fact, nothing else |
| `ensure_index()` | Creates the vector index Neo4j needs to search quickly |
| `store_for_memory()` | Writes the numbers onto the memory node, so they share its id |
| `search()` | Finds this subject's memories closest in meaning to some text |

---

## 12. Done

| Requirement | Line |
|---|---|
| Return stable ID, graph version, policy state | `abc.md:306` |
| Versioned nodes with valid-time, source, confidence, status | `abc.md:117` |
| Contradiction | `abc.md:118`, `:189` |
| Supersession | `abc.md:118` |
| Expiry | `abc.md:118`, `:133` |
| Explicit correction | `abc.md:118` |
| Subject isolation at the query boundary | `abc.md:119` |
| Graph constraints and idempotent upserts | `abc.md:120` |
| Embeddings under the same memory id | `abc.md:122`, `:190` |
| Approved fields only | `abc.md:122` |
| Entities resolved to canonical ids | `abc.md:113` |
| Policy class from the registry | `abc.md:115` |
| Repeated evidence strengthens rather than duplicates | `abc.md:49` |
| Audit event | `abc.md:460` |

---

## 13. Pending

| Missing | Requirement |
|---|---|
| Expiry is run by hand, not on a schedule | `abc.md:118` |
| Contradiction is found only when entity sets match exactly. "Loves country" and "no country songs on Fridays" would not be seen as clashing | `abc.md:118` |
| No golden set for contradiction handling | `abc.md:148` |

One deviation, stated:

| Ours | Note |
|---|---|
| `embedding_384` property | The specification does not name a property. Ours avoids the existing 64-dimension index rather than dropping it. |
