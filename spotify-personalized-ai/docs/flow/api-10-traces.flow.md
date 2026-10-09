# API 10 — `GET /v1/traces/{trace_id}` — Code Flow

Where the request starts, which function runs, what it calls next, where
the data goes, and what comes back.

> `abc.md:320` — *"Return authorized retrieval and policy decisions with
> sensitive fields redacted."*

---

## Two halves: writing and reading

Unlike the other endpoints, most of this one's work happens somewhere
else. APIs 4 and 5 write the decisions down as they take them; this reads
them back.

---

## Half one — the decisions get recorded

```
POST /v1/memories/search      (API 4)
POST /v1/context/compose      (API 5)
  │
  └─ ... the request finishes and replies ...
       │
       └─ trace.record_search()       [background]   memory/trace.py
            │  called after the reply, so recording never slows anything
            │
            ├─ FOR EACH included memory:
            │    trace.record_decision(stage="ranking", "included")
            │      ├─ trace._top_signal()   which signal weighed most
            │      └─                     ───────────► POSTGRES
            │
            └─ FOR EACH dropped memory:
                 trace.record_decision(stage="policy", "excluded")
                   with the reason it was dropped
                   └──────────────────────────────────► POSTGRES

Written: memory ids, scores, reasons, timestamps.
NOT written: the memory text. Ever.
```

---

## Half two — reading it back

```
GET /v1/traces/{trace_id}?subject_id=user_001
  │
  ├─ STEP 1 ── errors.add_correlation_id()          memory/errors.py
  ├─ STEP 2 ── auth.authenticate()                  memory/auth.py    → 401
  │
  └─ STEP 3 ── api.get_trace()                      memory/api.py
       │
       ├─ auth.bind_subject()                       memory/auth.py    → 403
       │     "authorized" in abc.md:320 means this check
       │
       ├─ trace.get_trace()                         memory/trace.py
       │     the retrieval and policy decisions
       │     subject in the query, so a trace id alone is not enough
       │     └───────────────────────────────────────► POSTGRES
       │
       ├─ trace.get_audit_for_trace()               memory/trace.py
       │     what the services did, sharing this correlation id
       │     └───────────────────────────────────────► POSTGRES
       │
       └─ IF neither found anything                                   → 404

RESPONSE
  {"trace_id": "cid_572507ca91c54483",
   "decisions": [
     {"stage": "ranking", "decision": "included",
      "memory_id": "mem_bc4f...", "score": 0.831,
      "reason": "strongest signal: explicitness"},
     {"stage": "policy", "decision": "excluded",
      "memory_id": "mem_4ca8...",
      "reason": "candidate_preference not allowed on player"}
   ],
   "actions": [{"action": "search.completed", "outcome": "found"}],
   "redacted": true}
```

---

## Every function, one line each

**Writing (called by APIs 4 and 5)**

| Function | File | What it does |
|---|---|---|
| `record_search()` | `memory/trace.py` | Writes a whole request's decisions in one go |
| `record_decision()` | `memory/trace.py` | Writes one decision — identifiers only, never text |
| `_top_signal()` | `memory/trace.py` | Names which signal weighed most, as the reason |

**Reading**

| Step | Function | File | What it does |
|---|---|---|---|
| 1 | `add_correlation_id()` | `memory/errors.py` | The trace id IS the correlation id |
| 2 | `authenticate()` | `memory/auth.py` | Checks the token and reads the subject |
| 3 | `get_trace()` | `memory/api.py` | The endpoint — checks access, reads both sources |
| 3.1 | `bind_subject()` | `memory/auth.py` | The "authorized" in abc.md:320 |
| 3.2 | `get_trace()` | `memory/trace.py` | Reads the retrieval and policy decisions |
| 3.3 | `get_audit_for_trace()` | `memory/trace.py` | Reads the service actions for this trace |

---

## Where the data goes

| Store | What happens |
|---|---|
| **PostgreSQL** | `trace_decision` written by 4 and 5, read here; `audit_log` read here |

Nothing else is touched. Writing is in the background; reading writes
nothing.

---

## The tension, and how it is resolved

Two requirements pull against each other.

**Useful** — a reviewer must see which memories were considered, used and
dropped, and why. `abc.md:101`.

**Redacted** — support staff reading a complaint must not end up reading
somebody's private memories. `abc.md:320`, `:322`.

**Resolved by recording identifiers, scores and reasons, never text.**

A reviewer sees that `mem_af02...` scored 0.831 and was included for being
the strongest explicit preference. They do not see that it says *"prefers
instrumental music while working"*. If they need that, they look the
memory up through a path that checks their authorisation.

A test pins this down: a memory with a distinctive phrase is stored, a
search is run, and the phrase must not appear **anywhere** in the trace.

---

## Why exclusions are recorded too

A trace showing only what was used cannot explain an **absence** — and
*"why didn't it know that?"* is the more common complaint.

```
"candidate_preference not allowed on player"
"confidence 0.2 below 0.35"
"would exceed the 400 token budget"
"more than 2 about topic_jazz"
```

Each is a different thing to investigate if it turns out to be wrong.

---

## What can go wrong, and where

| Response | From |
|---|---|
| 401 `UNAUTHENTICATED` | Step 2 |
| 403 `SUBJECT_MISMATCH` | Step 3.1 — the token is for another subject |
| 404 `NOT_FOUND` | Step 3.4 — no such trace **for this subject** |
| 200 | The decisions and actions, redacted |

The 404 covers both "no such trace" and "somebody else's trace", on
purpose. Telling them apart would confirm that another subject's trace id
is real.
