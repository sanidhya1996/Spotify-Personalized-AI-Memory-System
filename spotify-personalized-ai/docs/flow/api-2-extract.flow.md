# API 2 — `POST /v1/memories/extract` — Code Flow

Where the request starts, which function runs, what it calls next, where
the data goes, and what comes back.

---

## The path

```
POST /v1/memories/extract
  │
  ├─ STEP 1 ── errors.add_correlation_id()          memory/errors.py
  │            gives the request a tracking number
  │
  ├─ STEP 2 ── auth.authenticate()                  memory/auth.py
  │            checks the token is real                        → 401
  │
  ├─ STEP 3 ── ExtractRequest(...)                  memory/models.py
  │            needs subject_id and event_id                   → 422
  │
  └─ STEP 4 ── api.extract_memories()               memory/api.py
       │
       ├─ auth.bind_subject()                       memory/auth.py
       │     is this token allowed to touch this subject?      → 403
       │
       ├─ cache.is_rate_limited()                   memory/cache.py
       │     └──────────────────────────────────────► REDIS    → 429
       │
       ├─ db.get_consent()                          memory/db.py
       │     consent may have changed since the event arrived  → 403
       │     └──────────────────────────────────────► POSTGRES
       │
       ├─ db.get_event()                            memory/db.py
       │     read the event back — subject in the query, so an
       │     event id alone is not enough                      → 404
       │     └──────────────────────────────────────► POSTGRES
       │
       ├─ model_client.propose_candidates()         memory/model_client.py
       │     build the fenced prompt, ask the model            → 503
       │     └──────────────────────────────────────► GEMINI
       │
       └─ extraction.extract()                      memory/extraction.py
            │  runs every proposal through the rules
            │
            ├─ FOR EACH proposal:
            │   extraction.validate()               memory/extraction.py
            │     │  six rules decide if it is acceptable
            │     │
            │     ├─ 1. is the memory type one of our five?
            │     ├─ 2. is the fact real text?
            │     │
            │     ├─ 3. extraction.looks_sensitive()
            │     │      blocks mood, health, religion, politics
            │     │
            │     ├─ 4. is confidence a number? clamp it to 0–1
            │     ├─ 5. is one action pretending to be a preference?
            │     │
            │     ├─ 6. entities.resolve_all()      memory/entities.py
            │     │      │  names → catalog ids
            │     │      ├─ entities.normalise()    strip case and accents
            │     │      ├─ entities.alias_table()  ──► data/catalog.yaml
            │     │      └─ entities.similarity()   score a near miss
            │     │
            │     └─ policy.classify()              memory/policy.py
            │            retention and surfaces ──► data/policy_registry.yaml
            │
            └─ dedup.deduplicate()                  memory/dedup.py
                 │  one fact said twice becomes one memory
                 ├─ dedup.signature()   what makes two memories the same
                 └─ dedup.merge()       keeps every source event id

       └─ db.record_audit()          [background]   memory/db.py
             └──────────────────────────────────────► POSTGRES

RESPONSE
  {"event_id": "evt_...",
   "candidates": [{"memory_type": "exclusion",
                   "fact": "Does not want country music",
                   "entities": [...], "confidence": 0.95}],
   "no_memory": false,
   "rejected": []}
```

---

## Every function, one line each

| Step | Function | File | What it does |
|---|---|---|---|
| 1 | `add_correlation_id()` | `memory/errors.py` | Gives the request a tracking number |
| 2 | `authenticate()` | `memory/auth.py` | Checks the token and reads the subject |
| 3 | *(automatic)* | `memory/models.py` | Requires `subject_id` and `event_id` |
| 4 | `extract_memories()` | `memory/api.py` | The endpoint — access checks, then the chain below |
| 4.1 | `bind_subject()` | `memory/auth.py` | Refuses another subject's data |
| 4.2 | `is_rate_limited()` | `memory/cache.py` | Counts calls this minute |
| 4.3 | `get_consent()` | `memory/db.py` | Re-checks consent, which may have changed |
| 4.4 | `get_event()` | `memory/db.py` | Reads the event, subject-scoped |
| 4.5 | `propose_candidates()` | `memory/model_client.py` | Asks the model — the only place a provider is called |
| 4.6 | `extract()` | `memory/extraction.py` | Runs every proposal through the rules, then dedups |
| — | `validate()` | `memory/extraction.py` | The six rules; returns the reason when one fails |
| — | `looks_sensitive()` | `memory/extraction.py` | Blocks mood, health, religion, politics, sexuality |
| — | `resolve_all()` | `memory/entities.py` | Turns written names into catalog ids |
| — | `normalise()` | `memory/entities.py` | Strips case, accents and punctuation before matching |
| — | `alias_table()` | `memory/entities.py` | Loads every known spelling from the catalog |
| — | `similarity()` | `memory/entities.py` | Scores a near miss, so small typos still match |
| — | `classify()` | `memory/policy.py` | Stamps sensitivity, retention and allowed surfaces |
| — | `deduplicate()` | `memory/dedup.py` | Collapses equivalent statements into one |
| — | `signature()` | `memory/dedup.py` | Decides what makes two memories "the same" |
| — | `merge()` | `memory/dedup.py` | Folds a duplicate in, keeping every source |
| 4.7 | `record_audit()` | `memory/db.py` | Records the outcome |

---

## Where the data goes

| Store | What happens |
|---|---|
| **PostgreSQL** | The event is **read**; an audit line is **written** |
| **Redis** | The rate counter |
| **Gemini** | The event content is sent, fenced as data |
| **`data/catalog.yaml`** | Read, to resolve names |
| **`data/policy_registry.yaml`** | Read, to set retention |

**Nothing is stored.** This endpoint decides; storing is API 3.

---

## The important part

**The model proposes. `validate()` disposes.**

Every proposal passes through six rules, and `return None` means forget
it. Whatever is rejected comes back in `rejected` with the reason, so
nothing disappears silently.

---

## What can go wrong, and where

| Response | From |
|---|---|
| 401 `UNAUTHENTICATED` | Step 2 |
| 403 `SUBJECT_MISMATCH` | Step 4.1 |
| 429 `RATE_LIMITED` | Step 4.2 |
| 403 `CONSENT_DENIED` | Step 4.3 |
| 404 `NOT_FOUND` | Step 4.4 — no such event for this subject |
| 503 `SERVICE_UNAVAILABLE` | Step 4.5 — the model could not be reached |
| 200 `no_memory: true` | Step 4.6 — nothing survived the rules |
| 200 with candidates | Step 4.6 |
