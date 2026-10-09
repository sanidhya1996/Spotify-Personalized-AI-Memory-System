# How `POST /v1/events` Works — Line by Line

Every line of the endpoint, in the order it runs, in plain words.

---

## The files

```
memory/
  models.py   what a valid event looks like
  auth.py     who is calling, and whose data they may touch
  errors.py   tracking numbers and error names
  config.py   reads .env, builds the database addresses
  db.py       talks to PostgreSQL
  cache.py    talks to Redis
  api.py      the endpoint that ties it together

scripts/
  make_token.py      prints a token so you can test by hand
  check_stores.py    are the databases reachable?
  cleanup_expired.py deletes events past their expiry

data/schemas/
  event_v1.json      the frozen event shape, guarded by a contract test
```

---

## The whole journey

```
   Spotify chat or player app
            │  sends an event automatically
            ▼
   ┌──────────────────────────────────────────────┐
   │ 0  Give the request a tracking number         │ errors.py
   ├──────────────────────────────────────────────┤
   │ 1  Is the token real?                         │ auth.py   → 401
   ├──────────────────────────────────────────────┤
   │ 2  Are all 9 fields valid?                    │ models.py → 422
   ├──────────────────────────────────────────────┤
   │ 3  Does the token own this subject?           │ auth.py   → 403
   ├──────────────────────────────────────────────┤
   │ 4  Are they sending too fast?                 │ cache.py  → 429
   ├──────────────────────────────────────────────┤
   │ 5  Is the version supported?                  │ api.py    → 400
   ├──────────────────────────────────────────────┤
   │ 6  Did the listener actually consent?         │ db.py     → 403
   ├──────────────────────────────────────────────┤
   │ 7  Have we seen this exact event before?      │ cache.py  → 200 duplicate
   ├──────────────────────────────────────────────┤
   │ 8  Save it                                    │ db + cache
   ├──────────────────────────────────────────────┤
   │ 9  Reply, then write the audit line           │ api.py    → 200
   └──────────────────────────────────────────────┘
```

Nothing is saved until step 8. Everything before it is a gate.
`abc.md:110`: *"Reject malformed, unauthenticated, out-of-policy, or
unsupported events before graph processing."*

---

## The request

```
POST /v1/events
Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
```

```json
{
  "schema_version": "1.0",
  "subject_id": "user_001",
  "event_type": "playback",
  "surface": "player",
  "locale": "en-US",
  "occurred_at": "2026-09-23T10:00:00Z",
  "consent_state": "granted",
  "source_event_id": "src_1",
  "idempotency_key": "key_1"
}
```

**Nobody types this.** The app fills it in when a listener presses play or
sends a message. The listener never sees it.

---

## STEP 0 — Give the request a tracking number

**File:** `errors.py`

```python
app.middleware("http")(errors.add_correlation_id)
```

"Middleware" means: run this around *every* request, before and after.

```python
cid = request.headers.get(HEADER) or f"cid_{uuid.uuid4().hex[:16]}"
token = correlation_id.set(cid)
```

Like a parcel tracking number. Every request gets `cid_a3f9c1d4...`, it
comes back in the response header, and it appears in every error and
every audit line.

When something goes wrong, you search for that one id and see the whole
story of that request.

If the caller already sent a tracking number, **we keep theirs** — that is
how one trace follows a request through several services.

`abc.md:322`: *"attaches a correlation identifier."*

---

## STEP 1 — Is the token real?

**File:** `auth.py` · **Fails with:** 401 `UNAUTHENTICATED`

This runs before the endpoint body, because of this:

```python
caller: Caller = Depends(authenticate)
```

`Depends(...)` means *run this first; if it refuses, my code never starts.*

### The cinema ticket

A ticket says **"Seat 12"** and carries a stamp only the cinema can make.
The usher checks the stamp, reads the seat, and lets you into that seat
only. Change the seat with a pen and the stamp stops matching.

A token is three parts: `header.payload.signature`. The middle is
readable:

```json
{"sub": "user_001", "svc": "chat-surface", "exp": 1790151125}
```

- `sub` — which subject this token may touch
- `svc` — which service is calling
- `exp` — when it stops working

```python
payload = jwt.decode(token, SECRET, algorithms=[ALGORITHM])
```

That one line does two checks:

1. **Recompute the stamp** from the payload plus `SECRET` (the one line in
   `.env`). Different from what the token carries → it was edited → 401.
2. **Check `exp`.** Expired → 401.

So nobody can change `sub` to someone else's id. They would need `SECRET`,
and it never leaves the server.

The expiry is why `abc.md:54` lists *"replay of stale tokens"* as a
threat: a token stolen today is useless tomorrow. Ours last 15 minutes.

> **Who gets a token, and how:** the API's callers are Spotify's services,
> which get a token minted with the shared secret - `abc.md:187` asks us to
> verify *service identity* and *subject scope*, never a password. People
> using the web app log in (`POST /auth/login`, `memory/accounts.py`), the
> pilot's stand-in for the Spotify session a real deployment would have.

---

## STEP 2 — Are all nine fields valid?

**File:** `models.py` · **Fails with:** 422 `VALIDATION_FAILED`

You will not find this check in `api.py`. It happens because of the type:

```python
def create_event(event: Event, ...)
                        ^^^^^
```

FastAPI reads the JSON and tries to build an `Event`. If it cannot, your
code never runs. The rules are in the model:

```python
surface: Literal["chat", "player", "search"]
locale: str = Field(min_length=2)
subject_id: str = Field(min_length=1)
```

`Literal[...]` means *only these values*.

### The error message is deliberately careful

```python
fields = sorted({".".join(str(p) for p in e["loc"][1:]) for e in exc.errors()})
return JSONResponse(422, {"detail": error(
    VALIDATION_FAILED, f"invalid or missing fields: {', '.join(fields)}")})
```

It says **which field** was wrong, never **what you sent**:

```json
{"code": "VALIDATION_FAILED", "message": "invalid or missing fields: surface"}
```

The value could be private, and `abc.md:322` says responses carry
identifiers and outcomes, not raw content. A test enforces it:

```python
assert "surface" in message        # names the field
assert "teleport" not in message   # never repeats the value
```

The `code` matters too. Before, a 422 was an untyped blob of prose. Now
an app can check `code == "VALIDATION_FAILED"` — that is what
`abc.md:322` means by *"stable codes"*: the string never changes.

---

## STEP 3 — Does the token own this subject?

**File:** `auth.py` · **Fails with:** 403 `SUBJECT_MISMATCH`

```python
bind_subject(caller, event.subject_id)
```

```python
if caller.subject_id != subject_id:
    raise HTTPException(403, detail={"code": "SUBJECT_MISMATCH", ...})
```

Two lines, and the most important two in the project.

The **token** says which subject it is for. The **body** says which
subject the event is about. They must match. Otherwise `user_002` could
hold a perfectly valid token and write into `user_001`'s history.

`abc.md` scoring says a cross-subject isolation failure blocks release
*regardless of aggregate score*. That whole requirement comes down to
this one comparison.

---

## STEP 4 — Are they sending too fast?

**File:** `cache.py` · **Fails with:** 429 `RATE_LIMITED`

```python
if cache.is_rate_limited(event.subject_id):
    raise deny(429, errors.RATE_LIMITED, ...)
```

```python
minute = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
key = config.redis_key("rate", subject_id, minute)

count = client().incr(key)
if count == 1:
    client().expire(key, 60)
return count > RATE_LIMIT_PER_MINUTE
```

One counter per subject per minute. The key contains the minute, so a new
minute means a new counter automatically.

`incr` adds one and returns the new total. When it comes back as `1` this
is the first call of the minute, so we give the counter 60 seconds to
live. It then deletes itself — **no cleanup job to write.**

Limit: 120 events per subject per minute.

`abc.md:356` lists rate limits as a security test area.

---

## STEP 5 — Is the schema version supported?

**File:** `api.py` · **Fails with:** 400 `UNSUPPORTED_SCHEMA_VERSION`

```python
if event.schema_version != SUPPORTED_SCHEMA_VERSION:
    raise deny(400, errors.UNSUPPORTED_SCHEMA_VERSION, ...)
```

`abc.md:107` asks for *"versioned event contracts"*; `abc.md:110` says to
reject unsupported ones.

**Why it matters later:** when the event shape changes, old callers keep
sending `1.0`. This line is how we notice, instead of silently
misreading their data.

---

## STEP 6 — Did the listener actually consent?

**File:** `db.py` · **Fails with:** 403 `CONSENT_DENIED`

```python
consent = db.get_consent(event.subject_id)

if consent is None:
    raise deny(403, errors.CONSENT_DENIED, "no consent record for this subject")

if consent != "granted":
    raise deny(403, errors.CONSENT_DENIED, f"consent is {consent}")
```

```python
def get_consent(subject_id):
    with connect() as conn:
        row = conn.execute(
            "SELECT state FROM consent WHERE subject_id = %s", (subject_id,)
        ).fetchone()
    return row[0] if row else None
```

### The point

The request body **also** has a `consent_state` field — and we ignore it
as a decision. It is what the caller *claims*. The database holds what is
*true*.

`abc.md:187` says the API **"verifies ... consent state"**. You cannot
verify a claim against nothing.

| Subject | Our database | Caller claims | Result |
|---|---|---|---|
| `user_001` | granted | granted | 200 saved |
| `user_004` | **denied** | granted | **403** |
| `user_005` | **paused** | granted | **403** |
| `user_999` | *no record* | granted | **403** |

The last row matters: **no record means no permission.** We never assume
consent we were not given.

---

## STEP 7 — Have we seen this event before?

**File:** `cache.py` · **Returns:** 200 with `duplicate: true`

```python
seen = cache.get_event_id(event.subject_id, event.idempotency_key)
if seen is not None:
    return EventAccepted(event_id=seen, duplicate=True)
```

### Why this exists

Networks fail. An app sends an event, the reply is lost, so it sends the
same event again. Without this we would store it twice, and the listener
would look twice as interested in something as they really are.

The `idempotency_key` is the caller saying *"this is the same event I
sent before, not a new one."*

### Why the subject is in the key

```python
config.redis_key("idem", subject_id, idempotency_key)
# -> spotifymem:idem:user_001:key_1
```

Callers choose their own keys, so two subjects will eventually pick the
same one. If the key were `key_1` alone:

- `user_001` sends `key_1` → saved, gets `evt_AAA`
- `user_002` sends `key_1` → **we hand them `evt_AAA`** and drop their
  event

One subject's identifier leaking to another, plus data loss. **This was a
real bug in this code**, found by testing exactly that case.
`abc.md:296` names the fix: *"subject partition keys."*

The `spotifymem:` prefix keeps these keys away from anything else using
the same Redis server.

---

## STEP 8 — Save it

**Files:** `db.py`, `cache.py`

```python
event_id = f"evt_{uuid.uuid4().hex[:12]}"

db.save_event(event_id, event.model_dump(mode="json"), caller.service_id)
cache.remember(event.subject_id, event.idempotency_key, event_id)
```

### The id

`uuid.uuid4()` is random enough that two will never collide. The `evt_`
prefix makes it obvious what kind of id it is in a log.

### Postgres — the permanent record

```sql
INSERT INTO ingested_event (
    event_id, subject_id, service_id, event_type, surface,
    locale, schema_version, source_event_id, idempotency_key,
    occurred_at, expires_at
) VALUES (...)
```

`service_id` comes from the **token**, not the body — a caller cannot
claim to be a different service.

`expires_at` is 30 days out. Memories will live a year.
`abc.md:109`: *"Separate raw event retention from memory retention; not
every event becomes a retrievable memory."*

### Redis — the short-term memory

```python
client().set(key, event_id, ex=IDEMPOTENCY_TTL_SECONDS)   # 24 hours
```

`ex=` makes Redis delete the key by itself after a day. That self-expiry
is exactly why `abc.md:222` puts idempotency in Redis rather than a
table that would grow forever.

### Why Postgres first, then Redis

If the process died between the two lines:

- **Our order** — event saved, key forgotten. A retry writes a second
  row. Untidy, nothing lost.
- **The other order** — key saved, event not. The retry is treated as a
  duplicate and the event is **lost forever**.

Duplicating is recoverable; losing is not. So the durable write goes
first.

---

## STEP 9 — Reply, then write the audit line

**File:** `api.py`

```python
background.add_task(
    db.record_audit,
    action="event.accepted",
    subject_id=event.subject_id,
    service_id=caller.service_id,
    outcome="accepted",
    correlation_id=errors.correlation_id.get(),
    event_id=event_id,
)

return EventAccepted(event_id=event_id)
```

`background.add_task` means *send the reply now, do this afterwards.*
The caller does not wait for the audit write.

`abc.md:324`: *"Keep the user path independent of downstream graph-write
latency."*

### What an audit line holds

```
action: event.accepted   subject: user_001   service: chat-surface
outcome: accepted        event_id: evt_86e0...   cid: cid_a3f9c1d4
```

Identifiers and outcomes. **No song, no message, no search text.** The
table has no column for them, on purpose — `abc.md:145`: *"Redact
sensitive payloads from logs while preserving identifiers needed for
investigation."*

### The bug worth remembering

Rejections are audited **inline**, not in the background:

```python
def deny(status, code, message):
    db.record_audit(...)          # written immediately
    return HTTPException(...)
```

The first version used a background task here too — and rejections were
**never recorded**. Background tasks only run when a response completes
normally; raising an exception replaces the response and the queued task
is thrown away.

A test caught it. Without that test we would have shipped an audit trail
that recorded successes only — exactly the half you do not need during an
incident.

---

## The reply

```json
{"event_id": "evt_86e00164b3a8", "accepted": true, "duplicate": false}
```

plus the header `X-Correlation-Id: cid_a3f9c1d4...`

---

## Cleaning up old events

`expires_at` is only a date written on a row. Something has to act on it:

```
python scripts/cleanup_expired.py
```
```
deleted 14 expired raw event(s)
```

```sql
DELETE FROM ingested_event WHERE expires_at < now()
```

Raw events only. Memories are untouched — they live on their own, longer
clock. In production this runs on a schedule.

---

## The metrics endpoint

`GET /metrics` — how the system is doing, not what anyone did.

```json
{
  "events": {"accepted": 1482, "rejected": 37, "duplicate": 12, "stored": 1482},
  "rejection_rate": 0.0244,
  "rejections_by_reason": {"CONSENT_DENIED": 31, "RATE_LIMITED": 6},
  "ingestion_lag_seconds": 2.4
}
```

Every number is counted from the `audit_log` table we already write, so
there is no separate counter to keep in step with reality.

**Ingestion lag** is how long ago the newest event arrived. If it starts
climbing, events have stopped coming in — usually a broken caller.

**Rejection rate** is the share of events we refused. A sudden jump
usually means a caller was deployed with a bug.

It needs a token like everything else, and it deliberately contains **no
subject ids and no event ids** — counts only. A test checks that:

```python
assert "user_001" not in body
assert "evt_" not in body
```

`abc.md:143` asks to monitor ingestion lag, write failures and policy
rejection rate. `abc.md:339` is the Overview screen that reads this.

---

## The contract test

**File:** `tests/test_contract.py` · **Frozen copy:**
`data/schemas/event_v1.json`

Other teams write code against our event shape. If we rename
`subject_id` tomorrow, every one of them breaks — and nothing in our
tests would notice, because our own tests would be renamed with it.

So the shape is frozen in a file:

```json
{
  "version": "1.0",
  "required": ["consent_state", "event_type", "idempotency_key", "locale",
               "occurred_at", "schema_version", "source_event_id",
               "subject_id", "surface"],
  "allowed_values": {
    "surface": ["chat", "player", "search"],
    "consent_state": ["denied", "granted", "paused"],
    "event_type": ["ai_interaction", "correction", "explicit_preference",
                   "follow", "playback", "save", "skip"]
  }
}
```

and compared against the live model:

```python
missing = frozen - live
assert not missing, f"required field(s) removed or renamed: {sorted(missing)}"
```

Three things it catches:

| Change | Why it breaks callers |
|---|---|
| A required field removed or renamed | Their events stop validating |
| A **new** required field added | They do not send it, so their events fail |
| An allowed value removed | They still send it |

That second one is easy to miss. Adding a required field feels safe — it
is not, for anyone already sending events.

When a change is genuinely wanted, the answer is a **new schema version**,
not an edit to this file. That is what `schema_version` in every event is
for, and why step 5 rejects unknown versions.

`abc.md:352`: *"Backward compatibility for event, API, MCP tool, graph,
vector, and context-package schemas."*

---

## Trying it yourself

```
python scripts/check_stores.py                          # databases up?
python scripts/make_token.py user_001                   # get a token
python -m uvicorn memory.api:app --reload --port 8000   # start the API
```

Then open http://127.0.0.1:8000/docs and press **Try it out**.

| Try this | You get |
|---|---|
| A valid event | 200, a new `event_id` |
| The same body again | 200, **same** id, `duplicate: true` |
| `surface` = `"teleport"` | 422 `VALIDATION_FAILED` |
| `schema_version` = `"0.9"` | 400 `UNSUPPORTED_SCHEMA_VERSION` |
| `user_001` token, `"subject_id": "user_002"` | 403 `SUBJECT_MISMATCH` |
| An event for `user_004` | 403 `CONSENT_DENIED` — our record says denied |
| 121 events in one minute | 429 `RATE_LIMITED` |
| Wait 15 minutes, try again | 401, token expired |
| `GET /metrics` with a token | counts, lag, rejection rate |

---

## What this endpoint does not do

| Missing | Meaning | Requirement |
|---|---|---|
| A real message broker | Background tasks die with the process; Kafka would not | `abc.md:207` |
| Dead-letter replay | No way to retry a failed event | `abc.md:144` |
| Scheduled cleanup | `cleanup_expired.py` is run by hand | `abc.md:109` |
| Performance tests | No throughput or backpressure test | `abc.md:360` |
| Resilience tests | Partial write and rollback are untested | `abc.md:362` |
| Encryption in transit | No TLS — belongs to the gateway at deploy time | `abc.md:163` |

And the event records only **that** something happened, not **what** —
there is no field for the track or the message. That field arrives with
endpoint 2, which has to classify content it can actually see
(`abc.md:112`).

Endpoints 2 through 10 are still placeholders returning fixed values.
