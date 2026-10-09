# `PATCH /v1/memories/{memory_id}`

Endpoint 6 of 10. Lets a listener fix or retire something the system
believes about them.

> `abc.md:313` — *"Correct, supersede, expire, or change an eligible
> memory under optimistic concurrency."*

---

## 1. What it is for

Endpoints 1–5 build a system that remembers. This is the first one that
lets somebody **push back**.

`abc.md:137` lists it among the user-control requirements: *"Provide
review, correction, deletion, pause, and opt-out paths with clear state
and propagation status."*

The Product Design Lead puts the standard plainly (`abc.md:56`): users
need *"a clear experience: 'Spotify remembered this preference,' with the
ability to correct or remove it."*

---

## 2. The request

```
PATCH /v1/memories/mem_af02dce95af44517
Authorization: Bearer <token>
```

**Correcting**

```json
{
  "subject_id": "user_001",
  "operation": "correct",
  "expected_version": 1,
  "fact": "Never liked jazz",
  "entities": ["jazz"],
  "confidence": 1.0
}
```

**Retiring**

```json
{
  "subject_id": "user_001",
  "operation": "expire",
  "expected_version": 1
}
```

---

## 3. The response

**Corrected** — a new memory, and the old one named

```json
{"memory_id": "mem_NEW", "graph_version": 1,
 "status": "active", "superseded": "mem_OLD"}
```

**Expired**

```json
{"memory_id": "mem_af02...", "graph_version": 2, "status": "expired"}
```

**Errors**

| Status | Code | When |
|---|---|---|
| 401 | `UNAUTHENTICATED` | Token missing, forged, or expired |
| 403 | `SUBJECT_MISMATCH` | Token belongs to a different subject |
| 403 | `CONSENT_DENIED` | The correction is a sensitive inference |
| 404 | `NOT_FOUND` | No such memory **for this subject** |
| **409** | **`CONFLICT`** | **The memory changed since you read it** |
| 422 | `VALIDATION_FAILED` | Unknown operation, or a correction with no fact |
| 429 | `RATE_LIMITED` | More than 120 requests this minute |

---

## 4. Optimistic concurrency

This is the requirement that shapes the endpoint.

The caller sends the version they **last saw**:

```json
{"operation": "expire", "expected_version": 1}
```

If the memory is now at version 2, somebody changed it in between, and the
request is refused:

```json
{"code": "CONFLICT", "message": "memory is at version 2, not 1"}
```

**Why it matters.** Two support staff open the same memory. One corrects
it. The other, still looking at the old screen, expires it a moment later.
Without this check the second write silently undoes the first, and neither
person knows.

**Why "optimistic".** We do not lock the memory while somebody reads it.
We let both attempts proceed and catch the clash at the moment of writing.
Locking would be safer in theory and much worse in practice — a screen
left open would block everybody else.

---

## 5. Two operations, neither of which deletes

| Operation | What happens to the old memory |
|---|---|
| `expire` | `status: expired`, `valid_to` set — **still readable** |
| `correct` | `status: superseded`, linked from the new one — **still readable** |

`abc.md:118` — *"without erasing audit history prematurely"*.

A correction is **not** an edit. The old fact is closed and a new one is
created, with a `SUPERSEDES` link between them:

```
"Prefers jazz"      status: superseded   valid_to: 2026-09-25
        ↑ SUPERSEDES
"Never liked jazz"  status: active       valid_to: null
```

So *"why did the system think I liked jazz?"* always has an answer.

Actually removing a memory is endpoint 7 — and even that keeps the audit
line.

---

## 6. The same rules as everywhere else

A correction goes through the same checks as a memory created any other
way. It is not a way around them:

| Check | Requirement |
|---|---|
| Sensitive inferences refused | `abc.md:53` |
| Entities resolved by us, not the caller | `abc.md:113` |
| Policy class from the registry | `abc.md:115` |
| Embedded under the same id | `abc.md:190` |
| Subject-scoped read and write | `abc.md:119` |

Knowing a memory id is not enough to patch it — the subject is in the
query itself.

---

## 6b. Call flow

See `docs/flow/api-6-patch.flow.md` for the step-by-step trace: which
function runs, what it calls next, and which store each step touches.

---

## 7. The functions this API uses

Names and why, not code.

**`memory/api.py`**

| Function | Why it exists |
|---|---|
| `update_memory()` | The endpoint: checks access and version, then corrects or expires |

**`memory/graph.py`**

| Function | Why it exists |
|---|---|
| `get_memory()` | Reads the memory and its current version, subject-scoped |
| `expire_one()` | Closes a memory now, without deleting it |
| `supersede()` | Closes the old fact and links the new one to it |

**Reused from earlier APIs**

| Function | Why it is used here |
|---|---|
| `auth.bind_subject()` | The same cross-subject check as every endpoint |
| `extraction.looks_sensitive()` | A correction must not smuggle in a sensitive fact |
| `entities.resolve_all()` | Names to catalog ids, as anywhere else |
| `policy.classify()` | Retention and surfaces for the new correction |
| `embeddings.store_for_memory()` | The correction becomes searchable too |
| `db.record_audit()` | Records that somebody changed something |

---

## 8. Tests

`tests/test_correct_delete_api.py` — the PATCH half.

The ones that matter:

- A **stale version is refused**, and the error says which version it is at
- Expiring **closes** the memory without deleting it
- A correction **supersedes**, and only the correction is active afterwards
- A sensitive correction is **refused**
- Knowing a memory id is **not enough** to patch it

---

## 9. Done

| Requirement | Line |
|---|---|
| Correct an eligible memory | `abc.md:313` |
| Expire an eligible memory | `abc.md:313` |
| Supersede rather than overwrite | `abc.md:118`, `:313` |
| Optimistic concurrency | `abc.md:313` |
| Stable code for conflict | `abc.md:322` |
| Correction paths for user control | `abc.md:137` |
| Subject isolation | `abc.md:119` |
| Sensitive inferences blocked | `abc.md:53` |
| Audit event | `abc.md:460` |

---

## 10. Pending

| Missing | Requirement |
|---|---|
| **"or change"** — only `correct` and `expire` are supported. A plain field edit would overwrite history, which `abc.md:118` forbids, so anything else is expressed as a correction. Worth confirming that reading is right | `abc.md:313` |
| **No propagation status** — a correction updates the graph and the vector, but does not report per-store state the way deletion does | `abc.md:137` |
| **Corrections do not feed ranking** — `abc.md:192` lists "correction status" as a rerank signal; superseded memories are excluded entirely rather than scored down | `abc.md:192` |
| **No golden set** for correction handling | `abc.md:148` |
