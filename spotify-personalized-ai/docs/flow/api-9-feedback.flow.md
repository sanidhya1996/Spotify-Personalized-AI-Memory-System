# API 9 — `POST /v1/feedback` — Code Flow

Where the request starts, which function runs, what it calls next, where
the data goes, and what comes back.

> `abc.md:318` — *"Record relevance, correction, rejection, or experience
> feedback **without self-validating model output**."*

---

## The path

```
POST /v1/feedback
  │
  ├─ STEP 1 ── errors.add_correlation_id()          memory/errors.py
  │            gives the request a tracking number
  │
  ├─ STEP 2 ── auth.authenticate()                  memory/auth.py    → 401
  │            checks the token is real
  │
  ├─ STEP 3 ── FeedbackRequest(...)                 memory/models.py  → 422
  │            kind must be one of four, sentiment one of three
  │
  └─ STEP 4 ── api.create_feedback()                memory/api.py
       │
       ├─ auth.bind_subject()                       memory/auth.py    → 403
       │
       ├─ cache.is_rate_limited()    ───────────────► REDIS           → 429
       │
       ├─ IF a memory was named:
       │    graph.get_memory()                      memory/graph.py
       │      is it this subject's?                                   → 404
       │      └──────────────────────────────────────► NEO4J
       │
       └─ feedback.record()                         memory/feedback.py
            │
            ├─ graph.get_memory()                   memory/graph.py
            │     what KIND of memory is this?
            │     └─────────────────────────────────► NEO4J
            │
            ├─ feedback.may_reinforce()             memory/feedback.py
            │     THE RULE:
            │       negative           → counts
            │       positive + stated  → counts
            │       positive + inferred → recorded, counts for nothing
            │
            ├─ INSERT INTO feedback                 memory/feedback.py
            │     with `reinforced` and the reason it was or was not
            │     └─────────────────────────────────► POSTGRES
            │
            └─ IF negative:
                 db.record_audit("feedback.negative")   memory/db.py
                   so retrieval can read it as a ranking signal
                   └───────────────────────────────► POSTGRES

       └─ db.record_audit()          [background]   memory/db.py
             └───────────────────────────────────────► POSTGRES

RESPONSE
  {"feedback_id": "fbk_a1b2c3d4",
   "recorded": true,
   "reinforced": false,
   "reinforce_reason": "candidate_preference was inferred by us; positive
                        feedback on our own output is not evidence for it"}
```

---

## Every function, one line each

| Step | Function | File | What it does |
|---|---|---|---|
| 1 | `add_correlation_id()` | `memory/errors.py` | Gives the request a tracking number |
| 2 | `authenticate()` | `memory/auth.py` | Checks the token and reads the subject |
| 3 | *(automatic)* | `memory/models.py` | Rejects an unknown kind or sentiment |
| 4 | `create_feedback()` | `memory/api.py` | The endpoint — checks access, then records |
| 4.1 | `bind_subject()` | `memory/auth.py` | Refuses another subject's data |
| 4.2 | `is_rate_limited()` | `memory/cache.py` | Counts calls this minute |
| 4.3 | `get_memory()` | `memory/graph.py` | Confirms the memory is this subject's |
| 4.4 | `record()` | `memory/feedback.py` | Stores the feedback and applies it only where allowed |
| — | `may_reinforce()` | `memory/feedback.py` | **The rule** — may this feedback count as evidence? |
| — | `new_feedback_id()` | `memory/feedback.py` | Makes the identifier |
| 4.5 | `record_audit()` | `memory/db.py` | Negative feedback becomes a ranking signal |

---

## Where the data goes

| Store | What happens |
|---|---|
| **PostgreSQL** | The feedback row, and an audit line |
| **Neo4j** | The memory is **read**, to find out what kind it is |
| **Redis** | The rate counter |

Nothing in the graph is changed. Feedback is recorded, not applied.

---

## The one decision that matters

`may_reinforce()` is four lines and the whole point of the endpoint:

```
negative feedback              → counts, always
positive + they said it        → counts
positive + we inferred it      → recorded, counts for nothing
```

**Why.** The model guesses something, the guess shapes a reply, the
listener likes the reply, the guess gets more confident. Repeat, and the
system becomes certain about something nobody ever said — on the strength
of its own output.

`abc.md:149` — *"without automatically reinforcing model-generated
claims."*

The reason is **stored alongside the feedback**, so a reviewer later can
see why a particular piece did or did not count.

---

## What can go wrong, and where

| Response | From |
|---|---|
| 401 `UNAUTHENTICATED` | Step 2 |
| 422 `VALIDATION_FAILED` | Step 3 — unknown kind or sentiment |
| 403 `SUBJECT_MISMATCH` | Step 4.1 |
| 429 `RATE_LIMITED` | Step 4.2 |
| 404 `NOT_FOUND` | Step 4.3 — the memory is not this subject's |
| 200 `reinforced: true` | It counted |
| 200 `reinforced: false` | Recorded, and deliberately counted for nothing |
