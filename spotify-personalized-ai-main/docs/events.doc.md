# `POST /v1/events` — Implementation Status

Endpoint 1 of 10. Accepts an interaction event from an AI surface.

Requirement references point at line numbers in `abc.md`.

---

## 1. What this endpoint is for

> "Accept an eligible interaction event; validate subject, consent, schema,
> idempotency, and source." — `abc.md:303`

An AI surface (chat, player, search) sends us something a listener did.
We check it is allowed, store it once, and nothing more. Deciding whether
it becomes a *memory* is endpoint 2's job — `abc.md:109` is explicit that
not every event becomes a retrievable memory.

---

## 2. The request

```
POST /v1/events
Authorization: Bearer <token>
Content-Type: application/json
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

All nine fields are required. Eight come from `abc.md:233`
(subject scope, surface, locale, timestamp, event type, consent state,
source identifier, idempotency key); `schema_version` carries the
"versioned event contract" of `abc.md:107`.

**Allowed values**

| Field | Values | Source |
|---|---|---|
| `event_type` | `ai_interaction`, `playback`, `save`, `follow`, `skip`, `explicit_preference`, `correction` | `abc.md:107` |
| `surface` | `chat`, `player`, `search` | `abc.md:108` |
| `consent_state` | `granted`, `denied`, `paused` | `abc.md:108` |
| `schema_version` | `1.0` | `abc.md:187` |

---

## 3. The responses

**Accepted — 200**
```json
{"event_id": "evt_86e00164b3a8", "accepted": true, "duplicate": false}
```

**Same key sent again by the same subject — 200**
```json
{"event_id": "evt_86e00164b3a8", "accepted": true, "duplicate": true}
```
Same id, nothing stored twice.

**Errors**

| Status | Code | When |
|---|---|---|
| 401 | `UNAUTHENTICATED` | Token missing, unknown, tampered with, or expired |
| 403 | `SUBJECT_MISMATCH` | Token belongs to a different subject than the body names |
| 403 | `CONSENT_DENIED` | `consent_state` is `denied` or `paused` |
| 400 | `UNSUPPORTED_SCHEMA_VERSION` | `schema_version` is not `1.0` |
| 422 | *(none yet — see pending #2)* | A field is missing or has a bad value |

---

## 4. Authentication

`abc.md:162` — *"Every read and write must bind to authenticated subject
and service identities."*

This endpoint takes **no password**. Its callers are Spotify's AI surfaces,
not people: `abc.md:187` says the API verifies *service identity* and
*subject scope* - two things, neither of them a password. The token says
both.

Where does a token come from? For a service, it is minted with the shared
secret (`scripts/make_token.py`). For a person using the web app, it comes
from logging in (`POST /auth/login`, `memory/accounts.py`) - the pilot's
stand-in for the Spotify session a real deployment would already have.

**How it works.** One secret lives in `.env`. It stamps tokens that carry
the subject and service inside them:

```json
{"sub": "user_001", "svc": "chat-surface", "exp": 1790151125}
```

We verify the stamp and read the subject out of the token. No list of
valid tokens is stored anywhere. Editing `sub` breaks the stamp, so a
caller cannot claim to be someone else.

Tokens expire after 15 minutes, which covers *"replay of stale tokens"*
from the threat model at `abc.md:54`.

**Get a token for testing:**
```
python scripts/make_token.py user_001
```

> **Choice, not requirement.** The requirements demand the outcome
> (authenticated subject + service, no replay). JWT, the 15-minute
> lifetime, and `make_token.py` are implementation choices — the dossier
> names none of them.

---

## 5. Order of checks

Everything is rejected before anything is stored, per `abc.md:110`.

```
1. Token valid?              no -> 401 UNAUTHENTICATED
2. Token owns this subject?  no -> 403 SUBJECT_MISMATCH
3. Body well-formed?         no -> 422
4. schema_version supported? no -> 400 UNSUPPORTED_SCHEMA_VERSION
5. consent granted?          no -> 403 CONSENT_DENIED
6. seen this key before?     yes -> 200, same event_id, duplicate: true
7. store it                       -> 200, new event_id
```

---

## 5b. Call flow — what actually runs, in order

Follow this and you have followed the request.

```
POST /v1/events
  │
  ├─1 errors.add_correlation_id()            memory/errors.py
  │     gives the request a tracking number, echoed back in the response
  │
  ├─2 auth.authenticate()                    memory/auth.py     [Depends]
  │     checks the token stamp, reads the subject out of it        -> 401
  │
  ├─3 Event model validation                 memory/models.py   [FastAPI]
  │     builds an Event from the JSON; fails if a field is wrong   -> 422
  │
  └─4 api.create_event()                     memory/api.py
        │
        ├─ auth.bind_subject()               memory/auth.py
        │    token's subject must equal the body's subject         -> 403
        │
        ├─ cache.is_rate_limited()           memory/cache.py
        │    counts this minute's calls  ──────────────────► REDIS  -> 429
        │
        ├─ (schema_version check, inline)                          -> 400
        │
        ├─ db.get_consent()                  memory/db.py
        │    reads OUR consent record   ──────────────────► POSTGRES -> 403
        │
        ├─ cache.get_event_id()              memory/cache.py
        │    seen this idempotency key?  ──────────────────► REDIS
        │      if yes: return the first event_id, store nothing    -> 200
        │
        ├─ db.save_event()                   memory/db.py
        │    writes the event + its expiry ────────────────► POSTGRES
        │
        ├─ cache.remember()                  memory/cache.py
        │    records the key for 24h    ──────────────────► REDIS
        │
        ├─ [background] queue.publish()      memory/queue.py
        │    hands the event id to the worker ─────────────► REDPANDA
        │
        └─ [background] db.record_audit()    memory/db.py
             writes what happened      ──────────────────► POSTGRES

  response: {"event_id": "evt_...", "accepted": true, "duplicate": false}
            header: X-Correlation-Id
```

**Everything marked `[background]` happens after the reply is sent**, so the
listener never waits for it (`abc.md:324`).

**Steps 1–3 run before your code.** The middleware and `Depends(...)` are
wired in when the app starts, which is why a 401 beats a 422: the token is
checked before the body is even parsed.

---

## 6. Files

| File | Job |
|---|---|
| `memory/models.py` | The `Event` shape and its allowed values |
| `memory/auth.py` | Mint a token, verify it, bind the subject |
| `memory/store.py` | Where events live |
| `memory/api.py` | The endpoint itself |
| `scripts/make_token.py` | Prints a token for Postman |

**Storage is two dicts**, in memory:
```python
events:      dict[str, dict]              # event_id -> event
idempotency: dict[tuple[str, str], str]   # (subject_id, key) -> event_id
```

The idempotency key is a **pair**. `abc.md:296` requires *"subject
partition keys"*. Without the subject in the key, two subjects choosing
the same idempotency key collide — the second gets the first's
`event_id` and their event is silently dropped. That is cross-subject
leakage, which `abc.md` scoring makes pass/fail.

---

## 6b. The functions this API uses

Names and why, not code. Full code is in the files named.

**`memory/api.py`**

| Function | Why it exists |
|---|---|
| `create_event()` | The endpoint itself: runs the seven checks in order, then stores |
| `deny()` | One place to refuse a request and record why, so no rejection goes unlogged |

**`memory/auth.py`**

| Function | Why it exists |
|---|---|
| `mint_token()` | Stamps a token for one subject; the gateway does this in production |
| `authenticate()` | Checks the stamp and reads the subject out of the token |
| `bind_subject()` | Refuses when the token's subject differs from the body's - the cross-subject check |

**`memory/db.py`** (PostgreSQL)

| Function | Why it exists |
|---|---|
| `connect()` | Borrows a pooled connection; a fresh one per request was 5x slower |
| `get_consent()` | Reads OUR consent record, so a caller cannot claim consent it does not have |
| `save_event()` | Writes the accepted event with its own expiry date |
| `get_event()` | Reads one back, subject-scoped, for extraction |
| `record_audit()` | Writes who did what with what outcome - identifiers only, never content |
| `delete_expired_events()` | Acts on expires_at; storing the date does nothing on its own |
| `ingestion_metrics()` | Counts for the Overview screen, derived from the audit trail |

**`memory/cache.py`** (Redis)

| Function | Why it exists |
|---|---|
| `get_event_id()` | Have we seen this idempotency key before, for this subject? |
| `remember()` | Records the key for 24 hours, so Redis forgets it by itself |
| `is_rate_limited()` | One self-expiring counter per subject per minute |

**`memory/errors.py`**

| Function | Why it exists |
|---|---|
| `add_correlation_id()` | Gives every request a tracking number and echoes it back |
| `error()` | One shape for every error body, so callers parse one thing |
| `handle_validation_error()` | Gives bad input a stable code, naming the field without repeating the value |

**`memory/config.py`**

| Function | Why it exists |
|---|---|
| `postgres_url()` / `redis_url()` | Builds a connection string from the pieces in .env |
| `redis_key()` | Prefixes every key, so this app cannot collide with another on the same Redis |
| `safe()` | Masks the password when a URL is printed or logged |

---

## 7. Tests

```
python -m pytest -q      ->  65 passed
```

| File | Covers |
|---|---|
| `tests/test_auth.py` | Tokens, forgery, expiry, cross-subject isolation |
| `tests/test_events.py` | Validation, consent, idempotency, storage |
| `tests/test_api.py` | All ten endpoints respond |
| `tests/test_smoke.py` | Package imports |

Three tests matter most, because they guard the pass/fail requirement:

- **Forgery** — take `user_002`'s token, change `sub` to `user_001`,
  re-sign with a different secret → 401, nothing stored.
- **Wrong subject** — a valid `user_002` token posting for `user_001`
  → 403, store stays empty.
- **Key collision** — two subjects, same idempotency key → two separate
  events, no shared identifier.

---

## 8. Done

| Requirement | Line |
|---|---|
| Nine typed fields | `abc.md:108`, `:233` |
| Validate subject | `abc.md:303` |
| Validate consent | `abc.md:187` |
| Validate schema version | `abc.md:187` |
| Validate idempotency, partitioned by subject | `abc.md:187`, `:296` |
| Validate source | `abc.md:303` |
| Validate timestamp | `abc.md:187` |
| Reject malformed | `abc.md:110` |
| Reject unauthenticated | `abc.md:110` |
| Reject out-of-policy | `abc.md:110` |
| Reject unsupported version | `abc.md:110` |
| Subject + service identity | `abc.md:162`, `:187` |
| No token replay | `abc.md:54` |
| Cross-subject isolation | `abc.md:162` — **pass/fail** |

---

## 9. Pending

| # | Missing | Requirement | Size |
|---|---|---|---|
| 1 | **Correlation identifier** — a tracking id per request, echoed in the response and in errors | `abc.md:322` | small |
| 2 | **Stable code on validation errors** — the 422 body has no `code`, so callers must parse prose | `abc.md:322` | small |
| 3 | **Audit events** — record every accept, reject and duplicate | `abc.md:460` | small |
| 4 | **Asynchronous queue** — reply first, process after, so the listener never waits on a graph write | `abc.md:187`, `:324` | medium |
| 5 | **Separate retention clocks** — raw events expire sooner than memories | `abc.md:109` | small |
| 6 | **Redacted logging** — logs carry identifiers and outcomes, never event content | `abc.md:322`, `:145` | small |
| 7 | **Dead-letter handling and idempotent replay** for recoverable failures | `abc.md:144` | medium |
| 8 | **Rate limits** | `abc.md:356` | small |
| 9 | **Retention enforcement** — nothing expires or is swept today | `abc.md:109` | medium |

### Why the pending list matters

`abc.md:460` scores the backend category (12 marks) on seven things:

> typed contracts · authentication · idempotency · **queue integration** ·
> **error handling** · stable APIs · **audit events**

**Four of seven are done.** The three missing ones are items 2, 3 and 4.

---

## 10. Deviations from the specification

Simplifications, agreed deliberately. Each is isolated to one file, so
swapping in real infrastructure will not touch the endpoint.

| Ours | Specification | Line |
|---|---|---|
| In-process dicts | Redis for idempotency and caching | `abc.md:222` |
| In-process dicts | PostgreSQL for operational state | `abc.md:219` |
| One service, one port | Six services behind a gateway | `abc.md:254` |
| Tokens minted by a script | Gateway mints them after verifying the Spotify session | `abc.md:187` |
| Secret in a local `.env` | Secret manager | `abc.md:246` |
