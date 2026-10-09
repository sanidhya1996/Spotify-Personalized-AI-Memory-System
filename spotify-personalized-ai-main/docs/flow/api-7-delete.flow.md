# API 7 — `DELETE /v1/memories/{memory_id}` — Code Flow

And API 8 — `GET /v1/deletions/{job_id}` — which reports how it went.

> `abc.md:315` — *"Start cross-store deletion and return a traceable job
> identifier."*
> `abc.md:317` — *"Report graph, vector, cache, operational-store, and
> backup-policy status."*

**These two carry more weight than any other endpoint.** `abc.md` scoring
says a deletion failure blocks release regardless of everything else. A
deletion that reports success while leaving data behind is the worst
outcome in the project.

---

## Why it is a job and not just a delete

The same memory lives in several places at once:

```
Neo4j        the memory node and its links
Neo4j        its 384 numbers, on that same node
Redis        anything cached about it
PostgreSQL   the events it came from, and its audit trail
backups      snapshots that cannot be edited in place
```

Any of those can fail while the others succeed. So "deleted" is not a yes
or no — it is **a status per store**. The caller gets a job id straight
away and can ask later whether every store really cleared.

---

## The path — API 7

```
DELETE /v1/memories/{memory_id}?subject_id=user_001
  │
  ├─ STEP 1 ── errors.add_correlation_id()          memory/errors.py
  ├─ STEP 2 ── auth.authenticate()                  memory/auth.py    → 401
  │
  └─ STEP 3 ── api.delete_memory()                  memory/api.py
       │
       ├─ auth.bind_subject()                       memory/auth.py    → 403
       │
       ├─ graph.get_memory()                        memory/graph.py
       │     does it exist, for THIS subject?
       │     └───────────────────────────────────────► NEO4J          → 404
       │
       ├─ deletion.revoke_eligibility()             memory/deletion.py
       │     FIRST, before any store is cleared:
       │     status → deleted, so it stops being retrievable
       │     └───────────────────────────────────────► NEO4J
       │
       ├─ deletion.create_job()                     memory/deletion.py
       │     writes the job row, every store 'pending'
       │     └───────────────────────────────────────► POSTGRES
       │
       ├─ deletion.run_job()         [background]   memory/deletion.py
       │    │   each store separately, each recorded separately
       │    │
       │    ├─ deletion.source_events_of()  ────────► NEO4J
       │    │     FIRST: which events did this come from?
       │    │     asked before the node is destroyed, or the answer
       │    │     comes back empty
       │    │
       │    ├─ deletion.delete_from_graph()  ───────► NEO4J
       │    │     DETACH DELETE — takes the vector with it
       │    │
       │    ├─ vector                                (same node, done)
       │    │
       │    ├─ deletion.delete_from_cache()  ───────► REDIS
       │    │     clears this subject's keys
       │    │
       │    ├─ deletion.delete_from_operational() ──► POSTGRES
       │    │     the events it came from
       │    │
       │    ├─ backup → "retained_by_policy"
       │    │     a snapshot cannot be edited; say so honestly
       │    │
       │    └─ deletion.set_store_status()  per store ► POSTGRES
       │
       └─ db.record_audit()          [background]   memory/db.py
             └───────────────────────────────────────► POSTGRES

RESPONSE (immediately, before the stores are cleared)
  {"job_id": "job_a1b2c3d4", "memory_id": "mem_...", "status": "accepted"}
```

---

## The path — API 8

```
GET /v1/deletions/{job_id}?subject_id=user_001
  │
  ├─ STEP 1 ── errors.add_correlation_id()
  ├─ STEP 2 ── auth.authenticate()                                    → 401
  │
  └─ STEP 3 ── api.get_deletion()                   memory/api.py
       ├─ auth.bind_subject()                                         → 403
       └─ deletion.get_job()                        memory/deletion.py
            reads the job, subject in the query
            └──────────────────────────────────────► POSTGRES         → 404

RESPONSE
  {"job_id": "job_a1b2c3d4",
   "memory_id": "mem_...",
   "status": "completed",
   "stores": {"graph": "deleted",
              "vector": "deleted",
              "cache": "deleted",
              "operational": "deleted",
              "backup": "retained_by_policy"},
   "requested_at": "...", "completed_at": "..."}
```

---

## Every function, one line each

| Function | File | What it does |
|---|---|---|
| `delete_memory()` | `memory/api.py` | The endpoint — revoke, make a job, reply |
| `get_deletion()` | `memory/api.py` | Reports how the job went, per store |
| `get_memory()` | `memory/graph.py` | Checks it exists for this subject |
| `revoke_eligibility()` | `memory/deletion.py` | Stops it being retrievable, immediately |
| `create_job()` | `memory/deletion.py` | Records that a deletion was asked for |
| `run_job()` | `memory/deletion.py` | Clears each store and records each outcome |
| `delete_from_graph()` | `memory/deletion.py` | Removes the node, its links and its vector |
| `delete_from_cache()` | `memory/deletion.py` | Clears this subject's Redis keys |
| `source_events_of()` | `memory/deletion.py` | Reads the source event ids before the node is destroyed |
| `delete_from_operational()` | `memory/deletion.py` | Removes the events it came from |
| `set_store_status()` | `memory/deletion.py` | Writes one store's outcome onto the job |
| `get_job()` | `memory/deletion.py` | Reads the job back, subject-scoped |
| `record_audit()` | `memory/db.py` | Records that a deletion happened |

---

## Where the data goes

| Store | What happens |
|---|---|
| **Neo4j** | The memory node is marked deleted, then removed with its vector |
| **Redis** | This subject's cached keys are cleared |
| **PostgreSQL** | The job is written and updated; the events are removed; the audit line stays |

---

## Three decisions worth understanding

**1. Eligibility is revoked first.**

`abc.md:97` lists *"Revokes retrieval eligibility"* before anything else.
Marking the memory deleted takes effect at once — every read filters on
`status: active` — so it stops being usable even if a later store is slow.

The listener's "delete this" is honoured immediately, and the tidying up
follows.

**2. The vector needs no separate deletion.**

The 384 numbers are a property **on the memory node**. `DETACH DELETE`
takes both. `abc.md:55` requires graph and vector to share a stable id
*"so erasure is complete"* — here that means there is no second store that
could be forgotten.

**3. Backups are reported honestly.**

A backup is an immutable snapshot. You cannot reach into one and remove a
row. So the status is `retained_by_policy`, not `deleted`.

`abc.md:140` says deletion covers backups *"according to policy"* — the
policy being that the memory leaves when that snapshot expires. Claiming
otherwise would be a lie in exactly the place lying is most damaging.

**4. Every store reports what actually happened.**

A store returns `deleted` only when something was removed, and
`nothing_to_delete` when there was nothing there. Reporting "deleted" for
a store that cleared nothing is how a broken deletion hides in plain
sight.

> **This was a real bug, and it shipped.** The first version of
> `delete_from_operational()` passed an empty list to the DELETE — so it
> matched no rows — and returned `"deleted"` anyway. The cause was order:
> `run_job()` destroyed the graph node first, so by the time the
> operational step ran, the memory's `source_event_ids` were already gone.
> The fix collects them **before** anything is destroyed. Three tests now
> cover it, including one that checks an unrelated event survives.

**And the audit line survives the deletion.** `abc.md:118` warns against
erasing audit history; a deletion with no record of having happened is
worse than no deletion at all.

---

## What can go wrong, and where

**API 7**

| Response | From |
|---|---|
| 401 `UNAUTHENTICATED` | Step 2 |
| 403 `SUBJECT_MISMATCH` | Step 3.1 |
| 404 `NOT_FOUND` | Step 3.2 — no such memory **for this subject** |
| 200 with a job id | The normal path |

**API 8**

| Response | From |
|---|---|
| 401 `UNAUTHENTICATED` | Step 2 |
| 404 `NOT_FOUND` | No such job for this subject |
| 200 `status: completed` | Every store cleared |
| 200 `status: failed` | At least one store failed — `error` says which |

Note there is **no partial success that looks like success**. If any store
fails, the job status is `failed`, even though the others worked.
