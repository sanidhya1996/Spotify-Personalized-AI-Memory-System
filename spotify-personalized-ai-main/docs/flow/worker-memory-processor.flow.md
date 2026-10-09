# The Memory Processor — Code Flow

Not an endpoint. A background worker that turns captured events into
stored memories, with nobody calling anything.

> `abc.md:188` — *"A memory processor classifies the event, extracts
> candidate facts into a typed schema, resolves canonical content and
> concept entities, and applies minimization and sensitivity rules."*
> `abc.md:257` lists `memory-processor/` as one of the six services.

---

## Why it exists

Without it, a memory only appears if somebody calls API 2 and then API 3
by hand. Fine for a demo. Useless in production.

With it, a Spotify surface makes **one call** — `POST /v1/events`, which
returns in about 400 ms — and the memories appear afterwards on their own.

---

## The path

```
POST /v1/events  (API 1, already finished replying)
  │
  └─ queue.publish()                              memory/queue.py
       puts {event_id, subject_id, correlation_id} on the topic
       └───────────────────────────────────────────► REDPANDA
             topic: interaction-events

         ... the listener is gone. Later, separately ...

python scripts/run_processor.py
  │
  └─ processor.run_once()                         memory/processor.py
       │
       ├─ queue.consumer()                        memory/queue.py
       │    opens a reader on the topic
       │    └──────────────────────────────────────► REDPANDA
       │
       └─ FOR EACH message on the queue:
            │
            ├─ processor.process_event()          memory/processor.py
            │    │
            │    ├─ db.get_event()                memory/db.py
            │    │    read the event, subject-scoped
            │    │    └─────────────────────────────► POSTGRES
            │    │    not found → stop, nothing stored
            │    │
            │    ├─ db.get_consent()              memory/db.py
            │    │    consent may have been withdrawn since capture
            │    │    └─────────────────────────────► POSTGRES
            │    │    not granted → stop, nothing stored
            │    │
            │    ├─ is there any content to classify?
            │    │    a skip or a follow has no words → stop
            │    │
            │    ├─ model_client.propose_candidates()  memory/model_client.py
            │    │    └─────────────────────────────► GEMINI
            │    │    unreachable → RAISES, so the message is dead-lettered
            │    │
            │    ├─ extraction.extract()          memory/extraction.py
            │    │    the six rules, entity resolution, policy, dedup
            │    │    ──► data/catalog.yaml, data/policy_registry.yaml
            │    │    nothing survives → stop, "no memory worth keeping"
            │    │
            │    └─ FOR EACH surviving candidate:
            │         processor.store_candidate()  memory/processor.py
            │           │
            │           ├─ policy.classify()      memory/policy.py
            │           │    ──► data/policy_registry.yaml
            │           │
            │           ├─ graph.find_about()     memory/graph.py
            │           │    └───────────────────────► NEO4J
            │           ├─ graph.contradicts()    memory/graph.py
            │           │
            │           ├─ ONE OF THREE:
            │           │   graph.supersede()     ──► NEO4J   a clash
            │           │   graph.strengthen()    ──► NEO4J   said again
            │           │   graph.create_memory() ──► NEO4J   new
            │           │
            │           └─ embeddings.store_for_memory()  memory/embeddings.py
            │                ├─ embeddings.embed() ──► SentenceTransformers
            │                └─                    ──► NEO4J
            │
            ├─ IF it raised:
            │    queue.publish_dead_letter()       memory/queue.py
            │      keeps the message for inspection and replay
            │      └──────────────────────────────► REDPANDA
            │            topic: interaction-events-dlq
            │
            ├─ db.record_audit()                  memory/db.py
            │    └──────────────────────────────────► POSTGRES
            │
            └─ consumer.commit()
                 mark this message done — only NOW, after the work

RESULT
  {"handled": 1, "failed": 0, "memories_stored": 2}
```

---

## Every function, one line each

| Function | File | What it does |
|---|---|---|
| `publish()` | `memory/queue.py` | Puts the event id on the topic; only identifiers, never content |
| `consumer()` | `memory/queue.py` | Opens a reader that remembers where it got to |
| `publish_dead_letter()` | `memory/queue.py` | Sets a failed message aside instead of losing it |
| `run_once()` | `memory/processor.py` | Reads the queue and processes what is on it |
| `process_event()` | `memory/processor.py` | The whole chain for one event |
| `store_candidate()` | `memory/processor.py` | Writes one approved memory, the same way API 3 does |
| `get_event()` | `memory/db.py` | Reads the event back, subject-scoped |
| `get_consent()` | `memory/db.py` | Re-checks consent, which may have changed |
| `propose_candidates()` | `memory/model_client.py` | Asks the model what memories this event suggests |
| `extract()` | `memory/extraction.py` | Applies the six rules, resolves entities, deduplicates |
| `classify()` | `memory/policy.py` | Stamps retention, sensitivity, allowed surfaces |
| `find_about()` | `memory/graph.py` | Existing memories about exactly these entities |
| `contradicts()` | `memory/graph.py` | Can these two memory types both be true? |
| `supersede()` | `memory/graph.py` | Closes the old fact, links the new one to it |
| `strengthen()` | `memory/graph.py` | Counts more evidence rather than storing a duplicate |
| `create_memory()` | `memory/graph.py` | Writes a new memory node |
| `store_for_memory()` | `memory/embeddings.py` | Writes the 384 numbers onto the same node |
| `record_audit()` | `memory/db.py` | Records what happened |

---

## Where the data goes

| Store | What happens |
|---|---|
| **Redpanda** | Read from `interaction-events`; failures written to `interaction-events-dlq` |
| **PostgreSQL** | Event and consent read; audit line written |
| **Neo4j** | The memory node, its entity links, its vector |
| **Gemini** | The event content is sent, fenced as data |

---

## Four things worth understanding

**Only identifiers go on the queue.** Not the event content. The worker
reads the words from Postgres using the id, so nothing private is copied
into a second place that would also have to be deleted.

**The message is marked done last.** `consumer.commit()` runs *after* the
work, so a crash mid-job means the event is picked up again rather than
silently skipped.

**A failure goes to the dead-letter topic.** It is not retried forever,
and not dropped. This was exercised for real: Gemini returned 503 during
testing, and the message was set aside intact.

**Consent is checked again here.** It may have been withdrawn between the
listener speaking and the worker running. `abc.md:53` wants it enforced
before memory is created, not only at the door.

---

## The worker has no special privilege

`db.get_event()` is called with the subject, exactly as an API caller
would. A worker that could read across subjects would be a hole in
`abc.md:119` that no endpoint test would ever catch.

There is a test for precisely that: asking the worker to process
`user_001`'s event as `user_002` returns "not found".

---

## Running it

```
python scripts/run_processor.py             one pass, then stop
python scripts/run_processor.py --forever   keep going
```

```
handled 1, failed 0, memories stored 2
```

Without it running, events are captured but never become memories.

---

## Tests

`tests/test_processor.py` — 16 tests, no model calls.

The ones that matter most:

- An event becomes a stored memory with **nobody calling extract or memories**
- Consent withdrawn **after** capture stops the memory
- The worker **cannot** read another subject's event
- A sensitive inference is refused, the same as at the endpoint
- A contradiction supersedes, exactly as it would through the API
- The same thing twice **strengthens** one memory rather than making two
- A model outage **raises**, so the message can be dead-lettered
- Only identifiers go on the queue — the content never does
