# `DELETE /v1/memories/{memory_id}`

Endpoint 7 of 10. Removes a memory from every place it lives.

> `abc.md:315` — *"Start cross-store deletion and return a traceable job
> identifier."*

**This endpoint carries more weight than any other.** `abc.md` scoring
says a deletion failure blocks release regardless of everything else.

---

## 1. What it is for

A listener says "forget that". This makes it true — everywhere.

`abc.md:97` sets out what must happen: *"Revokes retrieval eligibility,
propagates deletion to graph and vector stores, and confirms completion
status."*

Three things, in that order. The endpoint does the first two and starts
the third.

---

## 2. Why it is a job, not a delete

The same memory lives in several places at once:

```
Neo4j        the memory node and its links
Neo4j        its 384 numbers, on that same node
Redis        anything cached about it
PostgreSQL   the events it came from
backups      snapshots that cannot be edited in place
```

Any of those can fail while the others succeed. So **"deleted" is not a
yes or no** — it is a status per store.

The caller gets a job id immediately and asks endpoint 8 later whether
every store really cleared.

---

## 3. The request

```
DELETE /v1/memories/mem_af02dce95af44517?subject_id=user_001
Authorization: Bearer <token>
```

The subject is required, and checked against the token. Knowing a memory
id is not enough.

---

## 4. The response

```json
{"job_id": "job_a1b2c3d4e5f6", "memory_id": "mem_af02...", "status": "accepted"}
```

`accepted`, not `deleted` — the stores are cleared afterwards. Ask
`GET /v1/deletions/{job_id}` for the outcome.

**Errors**

| Status | Code | When |
|---|---|---|
| 401 | `UNAUTHENTICATED` | Token missing, forged, or expired |
| 403 | `SUBJECT_MISMATCH` | Token belongs to a different subject |
| 404 | `NOT_FOUND` | No such memory **for this subject** |

---

## 5. Eligibility is revoked first

`abc.md:97` lists *"Revokes retrieval eligibility"* before anything else,
and the order matters.

Before any store is touched, the memory is marked `deleted` in the graph.
Every read filters on `status: active`, so it **stops being usable
immediately** — even if a later store is slow or fails.

The listener's request is honoured at once. The tidying up follows.

---

## 6. What happens in each store

| Store | What is done |
|---|---|
| **Graph** | `DETACH DELETE` — the node and all its links |
| **Vector** | Nothing separate: the 384 numbers are a property **on that node**, so they go with it |
| **Cache** | Every Redis key for this subject is cleared |
| **Operational** | The events this memory came from are deleted |
| **Backups** | Reported as `retained_by_policy` — see below |

**The vector needs no separate deletion**, and that is by design.
`abc.md:55` requires graph and vector to share a stable id *"so erasure is
complete"*. Here they are the same node, so there is no second store that
could be forgotten or drift out of step.

**Backups cannot be edited in place.** A backup is an immutable snapshot;
you cannot reach into last night's copy and remove a row. `abc.md:140`
says backups are handled *"according to policy"* — the policy being that
the memory leaves when that snapshot expires. Reporting `deleted` would be
a lie in the place where lying does most damage.

**The audit line survives.** `abc.md:118` warns against erasing audit
history; a deletion with no record of having happened is worse than no
deletion.

---

## 7. The bug this endpoint already had

Worth recording, because it is the exact failure this endpoint exists to
avoid.

The first version of `delete_from_operational()` passed an **empty list**
to its DELETE — matching no rows — and returned `"deleted"` anyway. The
listener's events sat untouched in Postgres while the job reported every
store clear.

**The cause was order.** `run_job()` destroyed the graph node first, so by
the time the operational step ran, the memory's `source_event_ids` were
already gone. `source_events_of()` now collects them **before** anything
is destroyed.

**What found it:** a linter warning that an argument was unused. Every
test passed, because they checked what the job *reported*, not what the
database actually held. The new tests query Postgres directly.

Every store now reports `deleted` only when rows actually went, and
`nothing_to_delete` otherwise.

---

## 7b. Call flow

See `docs/flow/api-7-delete.flow.md` for the step-by-step trace.

---

## 8. The functions this API uses

Names and why, not code.

**`memory/api.py`**

| Function | Why it exists |
|---|---|
| `delete_memory()` | The endpoint: revoke, create the job, reply, clear in the background |

**`memory/deletion.py`**

| Function | Why it exists |
|---|---|
| `revoke_eligibility()` | Stops the memory being retrievable, immediately |
| `create_job()` | Records that a deletion was asked for, before any of it is done |
| `source_events_of()` | Reads the source event ids **before** the node is destroyed |
| `run_job()` | Clears each store and records each outcome separately |
| `delete_from_graph()` | Removes the node, its links and its vector |
| `delete_from_cache()` | Clears this subject's Redis keys |
| `delete_from_operational()` | Removes the events the memory came from |
| `set_store_status()` | Writes one store's outcome onto the job |
| `new_job_id()` | Makes the traceable identifier |

**Reused**

| Function | Why it is used here |
|---|---|
| `graph.get_memory()` | Confirms the memory exists for this subject |
| `auth.bind_subject()` | The same cross-subject check as every endpoint |
| `db.record_audit()` | Records that a deletion happened, and survives it |

---

## 9. Tests

`tests/test_correct_delete_api.py` — the DELETE half.

The ones that matter:

- The memory is **gone from the graph**
- It **stops being retrievable** — a search no longer returns it
- The **vector goes with it** — a semantic search no longer finds it
- The **source events are actually deleted** from Postgres
- An **unrelated event survives** — only this memory's events go
- A store with nothing in it **does not claim a deletion**
- Knowing a memory id is **not enough** to delete it
- The deletion is **audited**

---

## 10. Done

| Requirement | Line |
|---|---|
| Start cross-store deletion, return a job id | `abc.md:315` |
| Revoke retrieval eligibility | `abc.md:97` |
| Propagate to graph and vector | `abc.md:97` |
| Deletion across graph, vector, cache, operational, backups | `abc.md:140` |
| Graph and vector share an id so erasure is complete | `abc.md:55` |
| Asynchronous work returns a job state | `abc.md:322` |
| Subject isolation | `abc.md:119` |
| Audit history survives the deletion | `abc.md:118` |
| Deletion paths for user control | `abc.md:137` |

---

## 11. Pending

| Missing | Requirement |
|---|---|
| **No subject-wide deletion** — this removes one memory. `abc.md:140` also asks for "subject-access and deletion workflows", meaning everything for one listener | `abc.md:140` |
| **No retry** for a store that failed. The job records the failure but nothing re-attempts it | `abc.md:144` |
| **Backup expiry is asserted, not verified** — we report the policy, but nothing checks a snapshot actually aged out | `abc.md:358` |
| **No deletion-propagation test against a real backup** | `abc.md:358` |
| **Deletion is not measured** — `abc.md:59` lists deletion propagation as an offline success metric | `abc.md:59` |
