# API 1 — `POST /v1/events` — Code Flow

Where the request starts, which function runs, what it calls next, where
the data goes, and what comes back.

---

## The path

```
POST /v1/events
  │
  ├─ STEP 1 ── errors.add_correlation_id()          memory/errors.py
  │            gives the request a tracking number
  │
  ├─ STEP 2 ── auth.authenticate()                  memory/auth.py
  │            checks the token is real                        → 401
  │
  ├─ STEP 3 ── Event(...)                           memory/models.py
  │            builds an Event from the JSON                   → 422
  │
  └─ STEP 4 ── api.create_event()                   memory/api.py
       │       the endpoint body, calling each of these in turn
       │
       ├─ auth.bind_subject()                       memory/auth.py
       │     is this token allowed to touch this subject?      → 403
       │
       ├─ cache.is_rate_limited()                   memory/cache.py
       │     have they sent too many this minute?              → 429
       │     └──────────────────────────────────────► REDIS
       │
       ├─ (schema_version check, written inline in api.py)
       │     is this event contract version supported?         → 400
       │
       ├─ db.get_consent()                          memory/db.py
       │     what does OUR record say about consent?           → 403
       │     └──────────────────────────────────────► POSTGRES
       │
       ├─ cache.get_event_id()                      memory/cache.py
       │     have we already seen this idempotency key?
       │     └──────────────────────────────────────► REDIS
       │     if found → reply with the first event_id, store nothing
       │
       ├─ db.save_event()                           memory/db.py
       │     write the event down, with its expiry date
       │     └──────────────────────────────────────► POSTGRES
       │
       ├─ cache.remember()                          memory/cache.py
       │     remember this key for 24 hours
       │     └──────────────────────────────────────► REDIS
       │
       ├─ queue.publish()            [background]   memory/queue.py
       │     hand the event id to the worker
       │     └──────────────────────────────────────► REDPANDA
       │
       └─ db.record_audit()          [background]   memory/db.py
             write down what happened
             └──────────────────────────────────────► POSTGRES

RESPONSE
  {"event_id": "evt_8fae2a5d3bbf", "accepted": true, "duplicate": false}
  header: X-Correlation-Id: cid_572507ca91c54483
```

---

## Every function, one line each

| Step | Function | File | What it does |
|---|---|---|---|
| 1 | `add_correlation_id()` | `memory/errors.py` | Gives the request a tracking number and echoes it back |
| 2 | `authenticate()` | `memory/auth.py` | Checks the token stamp and reads the subject out of it |
| 3 | *(automatic)* | `memory/models.py` | FastAPI builds an `Event`; a bad field never reaches our code |
| 4 | `create_event()` | `memory/api.py` | The endpoint — runs every check below, in order |
| 4.1 | `bind_subject()` | `memory/auth.py` | Refuses if the token belongs to a different subject |
| 4.2 | `is_rate_limited()` | `memory/cache.py` | Counts this subject's calls this minute |
| 4.3 | *(inline)* | `memory/api.py` | Rejects an event contract version we do not support |
| 4.4 | `get_consent()` | `memory/db.py` | Reads our own consent record, ignoring the caller's claim |
| 4.5 | `get_event_id()` | `memory/cache.py` | Looks up whether this idempotency key was used before |
| 4.6 | `save_event()` | `memory/db.py` | Writes the event and the date it expires |
| 4.7 | `remember()` | `memory/cache.py` | Stores the idempotency key with a 24-hour life |
| 4.8 | `publish()` | `memory/queue.py` | Puts the event id on the queue for the worker |
| 4.9 | `record_audit()` | `memory/db.py` | Records the outcome — identifiers only, never content |

---

## Where the data goes

| Store | What lands there |
|---|---|
| **PostgreSQL** | The event itself, and the audit line |
| **Redis** | The idempotency key (24h), the rate counter (60s) |
| **Redpanda** | The event id, for the worker to pick up |

---

## Two things worth knowing

**Steps 1–3 run before the endpoint body.** They are wired in when the app
starts, which is why a bad token gives 401 even when the body is also
wrong — the token is checked before the JSON is parsed.

**`[background]` means after the reply is sent.** The listener does not
wait for the queue or the audit write.

---

## What can go wrong, and where

| Response | From |
|---|---|
| 401 `UNAUTHENTICATED` | Step 2 |
| 422 `VALIDATION_FAILED` | Step 3 |
| 403 `SUBJECT_MISMATCH` | Step 4.1 |
| 429 `RATE_LIMITED` | Step 4.2 |
| 400 `UNSUPPORTED_SCHEMA_VERSION` | Step 4.3 |
| 403 `CONSENT_DENIED` | Step 4.4 |
| 200 `duplicate: true` | Step 4.5 |
| 200 with a new `event_id` | Step 4.6 onward |
