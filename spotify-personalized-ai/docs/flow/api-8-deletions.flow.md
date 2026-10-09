# API 8 — `GET /v1/deletions/{job_id}` — Code Flow

Where the request starts, which function runs, what it calls next, where
the data goes, and what comes back.

> `abc.md:317` — *"Report graph, vector, cache, operational-store, and
> backup-policy status."*

---

## What it does

**Answers one question: did the deletion actually finish?**

API 7 starts a deletion and hands back a job id. This is how you find out
whether every store really cleared — or which one did not.

---

## The path

```
GET /v1/deletions/{job_id}?subject_id=user_001
  │
  ├─ STEP 1 ── errors.add_correlation_id()          memory/errors.py
  │            gives the request a tracking number
  │
  ├─ STEP 2 ── auth.authenticate()                  memory/auth.py    → 401
  │            checks the token is real
  │
  └─ STEP 3 ── api.get_deletion()                   memory/api.py
       │
       ├─ auth.bind_subject()                       memory/auth.py    → 403
       │     is this token allowed to see this subject's jobs?
       │
       └─ deletion.get_job()                        memory/deletion.py
             reads the job row, subject in the query so a job id
             alone is not enough
             └───────────────────────────────────────► POSTGRES      → 404

RESPONSE
  {"job_id": "job_a1b2c3d4",
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
   "error": null}
```

Short flow, because the work already happened. This is a read.

---

## Every function, one line each

| Step | Function | File | What it does |
|---|---|---|---|
| 1 | `add_correlation_id()` | `memory/errors.py` | Gives the request a tracking number |
| 2 | `authenticate()` | `memory/auth.py` | Checks the token and reads the subject |
| 3 | `get_deletion()` | `memory/api.py` | The endpoint — checks access, then reads the job |
| 3.1 | `bind_subject()` | `memory/auth.py` | Refuses another subject's jobs |
| 3.2 | `get_job()` | `memory/deletion.py` | Reads the job row, subject-scoped |

---

## Where the data goes

| Store | What happens |
|---|---|
| **PostgreSQL** | The `deletion_job` row is **read** |

Nothing is written. This endpoint only reports.

---

## What the statuses mean

**The job**

| `status` | Means |
|---|---|
| `pending` | Still running |
| `completed` | Every store finished without error |
| `failed` | At least one store failed — `error` says which |

**Each store**

| Value | Means |
|---|---|
| `pending` | Not attempted yet |
| `deleted` | Something was actually removed |
| `nothing_to_delete` | There was nothing there to remove |
| `failed` | It was attempted and did not work |
| `retained_by_policy` | Backups only — see below |

---

## Why five stores and not one flag

A memory lives in several places, and any of them can fail while the
others succeed:

```
Neo4j        the memory node, its links, its 384 numbers
Redis        anything cached about it
PostgreSQL   the events it came from
backups      snapshots that cannot be edited
```

Collapsing that into one `"deleted": true` would hide a partial failure —
and `abc.md` scoring makes a deletion failure block release regardless of
everything else. So each store answers for itself.

---

## Why `deleted` and `nothing_to_delete` are different

This distinction exists because of a real bug.

The first version of `delete_from_operational()` removed nothing and
reported `"deleted"` anyway. The job looked perfectly healthy while the
listener's events sat untouched in Postgres.

A store now says `deleted` **only when rows actually went**. If there was
nothing to remove, it says so. The difference between "I removed it" and
"there was nothing to remove" is exactly the difference between a working
deletion and a broken one pretending.

---

## Why backups say `retained_by_policy`

A backup is an immutable snapshot. You cannot reach into last night's
backup and remove a row.

`abc.md:140` says deletion covers backups *"according to policy"* — the
policy being that the memory leaves when that snapshot expires on its own
schedule.

Reporting `deleted` would be a lie, in the one place where lying does the
most damage. So it reports what is true.

---

## What can go wrong, and where

| Response | From |
|---|---|
| 401 `UNAUTHENTICATED` | Step 2 |
| 403 `SUBJECT_MISMATCH` | Step 3.1 — the token is for another subject |
| 404 `NOT_FOUND` | Step 3.2 — no such job **for this subject** |
| 200 | The job, with a line per store |

Note the 404 covers two cases deliberately: a job that does not exist, and
a job that belongs to somebody else. Telling them apart would confirm that
another subject's job id is real.
