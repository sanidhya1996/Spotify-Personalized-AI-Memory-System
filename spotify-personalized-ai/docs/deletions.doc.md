# `GET /v1/deletions/{job_id}`

Endpoint 8 of 10. Reports whether a deletion actually finished.

> `abc.md:317` — *"Report graph, vector, cache, operational-store, and
> backup-policy status."*

---

## 1. What it is for

Endpoint 7 starts a deletion and hands back a job id. This answers the
only question that matters afterwards: **did every store really clear?**

It exists because deletion can partly fail. `abc.md:97` ends with
*"confirms completion status"* — confirmation is a separate step from
doing, and it has to be asked for.

The `memory-controls` screen polls this to show a listener that their
"forget that" actually happened, rather than asking them to trust it.

---

## 2. The request

```
GET /v1/deletions/job_a1b2c3d4e5f6?subject_id=user_001
Authorization: Bearer <token>
```

---

## 3. The response

```json
{
  "job_id": "job_a1b2c3d4e5f6",
  "memory_id": "mem_af02dce95af44517",
  "status": "completed",
  "stores": {
    "graph":       "deleted",
    "vector":      "deleted",
    "cache":       "deleted",
    "operational": "deleted",
    "backup":      "retained_by_policy"
  },
  "requested_at": "2026-09-25T12:00:00Z",
  "completed_at": "2026-09-25T12:00:01Z",
  "error": null
}
```

**Errors**

| Status | Code | When |
|---|---|---|
| 401 | `UNAUTHENTICATED` | Token missing, forged, or expired |
| 403 | `SUBJECT_MISMATCH` | Token belongs to a different subject |
| 404 | `NOT_FOUND` | No such job **for this subject** |

The 404 covers two cases on purpose: a job that does not exist, and a job
belonging to somebody else. Telling them apart would confirm that another
subject's job id is real.

---

## 4. What the statuses mean

**The job**

| `status` | Means |
|---|---|
| `pending` | Still running |
| `completed` | Every store finished without error |
| `failed` | At least one store failed — `error` names it |

**Each store**

| Value | Means |
|---|---|
| `pending` | Not attempted yet |
| `deleted` | Something was actually removed |
| `nothing_to_delete` | There was nothing there to remove |
| `failed` | Attempted, and did not work |
| `retained_by_policy` | Backups only |

---

## 5. Why five stores and not one flag

A memory lives in several places, and any can fail while the others
succeed. Collapsing that into `"deleted": true` would hide a partial
failure.

`abc.md` scoring makes a deletion failure block release **regardless of
aggregate score**, so this is one of the few places where reporting less
detail would be a serious fault rather than a simplification.

**And a partial failure is never reported as success.** If any store
fails, the job is `failed`, even though the others worked.

---

## 6. Why `deleted` and `nothing_to_delete` are different

This distinction exists because of a real bug in endpoint 7.

`delete_from_operational()` removed nothing and reported `"deleted"`
anyway. The job looked perfectly healthy while the listener's events sat
untouched in Postgres. Every test passed, because they checked what the
job *said*.

A store now reports `deleted` **only when rows actually went**.

The difference between *"I removed it"* and *"there was nothing to
remove"* is exactly the difference between a working deletion and a broken
one pretending. A status that cannot express the second will always be
able to hide the first.

---

## 7. Why backups say `retained_by_policy`

A backup is an immutable snapshot. You cannot reach into last night's copy
and remove a row.

`abc.md:140` says deletion covers backups *"according to policy"* — the
policy being that the memory leaves when that snapshot expires on its own
schedule.

Reporting `deleted` would be a lie in the one place where lying does the
most damage. So it reports what is true, and names the reason.

---

## 7b. Call flow

See `docs/flow/api-8-deletions.flow.md` for the step-by-step trace.

---

## 8. The functions this API uses

Names and why, not code.

**`memory/api.py`**

| Function | Why it exists |
|---|---|
| `get_deletion()` | The endpoint: checks access, reads the job, reports per store |

**`memory/deletion.py`**

| Function | Why it exists |
|---|---|
| `get_job()` | Reads the job row with the subject in the query |

**Reused**

| Function | Why it is used here |
|---|---|
| `auth.bind_subject()` | The same cross-subject check as every endpoint |

A short list, because the work already happened. This endpoint only reads.

---

## 9. Tests

`tests/test_correct_delete_api.py` — the status half.

- The job reports **every store** — graph, vector, cache, operational, backup
- A finished job says `completed`, with a `completed_at`
- Each store reports **its own** outcome
- Backups are reported **honestly** as `retained_by_policy`
- One subject **cannot read** another's job
- An unknown job is **not found**

---

## 10. Done

| Requirement | Line |
|---|---|
| Report graph, vector, cache, operational-store and backup-policy status | `abc.md:317` |
| Confirms completion status | `abc.md:97` |
| Asynchronous work returns a job state | `abc.md:322` |
| Propagation status visible to the listener | `abc.md:137` |
| Subject isolation | `abc.md:119` |

---

## 11. Pending

| Missing | Requirement |
|---|---|
| **No listing** — you can read one job by id, but not "show me this subject's deletions". The controls screen would want that | `abc.md:137` |
| **No retry** — a failed store is reported and left. Nothing re-attempts it | `abc.md:144` |
| **Backup status is asserted, not verified** — we report the policy rather than checking a snapshot aged out | `abc.md:358` |
| **`pending` is rarely seen** — the stores clear in a background task that usually finishes before anyone asks. With a slower store it would matter more | — |
| **No deletion-propagation metric** — `abc.md:59` lists it as an offline success measure | `abc.md:59` |
