# Spotify Personalized AI Memory System

A governed memory layer for Spotify's AI surfaces. It captures what a listener
says, turns it into typed, time-bounded memories in a graph, finds only what
is relevant to the current request, and hands the AI a small, sourced context
package - with full listener control over correction, pause, opt-out and
deletion.

A **product capability pilot, not a general-purpose behaviour archive.** Every
remembered fact carries a source, a confidence score, a policy class and a
valid time, and can be corrected or deleted.

---

## Links

| | |
|---|---|
| **Live app** | https://spotifyfrontend11.netlify.app |
| **Live API** | https://spotify-personalized-ai.onrender.com - `/docs`, `/health/stores` |
| **Demo video** |https://www.loom.com/share/6507ef7e4ca4416b8781151a5a94a4e1|
| **Frontend repository** | https://github.com/omsemwal/spotify-fronted |

---

## Try it

Open the app and **sign up** with a user id nobody has (lower-case letters,
digits, `_`) and a password of 8+ characters. You only ever see your own data.

Then on **Context preview**: *Tell Spotify's AI* something like "I love Arijit
Singh romantic songs, but no heavy metal", wait a few seconds, and ask "play
something romantic". You see which memories were found, how they ranked, what
policy removed, the exact package the AI receives - and a songs demo.

---

## What it does

| | |
|---|---|
| **Capture** | `POST /v1/events` validates subject, consent, schema version, idempotency and source, then queues the event and replies at once |
| **Understand** | The worker asks Gemini for typed candidates, then deterministic rules decide: type, entities, confidence, policy class. A model's output alone never creates a memory |
| **Remember** | Neo4j temporal graph: `valid_from`, `valid_to`, `recorded_at`, confidence, status. Corrections supersede and keep history; repeats strengthen, not duplicate |
| **Retrieve** | Hybrid graph + vector search, ranked by six weighted signals, filtered by surface policy, capped for diversity |
| **Compose** | A token-budgeted package with provenance and relevance reasons; stored text fenced as data against prompt injection; a deterministic no-memory fallback, also when a store is down |
| **Control** | Review, correct, delete (across every store, with per-store status), pause, opt out |
| **Govern** | Retention by memory type, region and age; consent checked before memory is used; every read and write bound to an authenticated subject; audit trail; traces with sensitive text redacted |

---

## Run it locally

Full instructions and troubleshooting: **[RUNNING.md](RUNNING.md)**.

**Once:**

```bash
pip install -r requirements.txt
cp .env.example .env                 # then add a Gemini key
docker compose up -d                 # postgres, redis, neo4j, redpanda
python scripts/setup.py              # tables, constraints, vector index, demo logins
```

**Then** - on Windows, one command:

```bash
./start-backend.cmd                  # databases, worker and API
```

or two terminals:

```bash
python -m uvicorn memory.api:app --port 8000      # the API
python scripts/run_processor.py --forever         # the worker - turns events into memories
```

API docs with "Try it out": **http://127.0.0.1:8000/docs**. Start the frontend
from its repository (`./start-frontend.cmd`), then open http://localhost:3000.

---

## Architecture

Two processes and four stores. On a free host the worker runs inside the API
process instead (`RUN_WORKER_IN_API=true`) - same code, one service.

```
Web app (frontend repo)        AI surfaces / MCP clients
          \                         /
           v                       v
     API  (memory/api.py)  - ten endpoints, login, consent, metrics
           |  POST /v1/events replies at once and queues the event
           v
     Redpanda (Kafka)  ->  dead-letter topic for failures
           |
           v
     Worker (scripts/run_processor.py) - classify, extract, policy check, write
           |
           v
     Neo4j       the temporal graph and its vector index (same memory id)
     PostgreSQL  accounts, consent, events, audit log, deletion jobs, traces
     Redis       idempotency keys, rate limits, login lockouts

     MCP server (memory/mcp_server.py) - the five tools a model may use
```

The listener never waits for a graph write (`abc.md:324`): the API accepts
the event and replies; the worker does the slow work afterwards.

End to end: [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md).

---

## APIs - all 10 required (`abc.md` §7.3)

| # | Endpoint | Doc |
|---|---|---|
| 1 | `POST /v1/events` | [events.doc.md](docs/events.doc.md) |
| 2 | `POST /v1/memories/extract` | [memories-extract.doc.md](docs/memories-extract.doc.md) |
| 3 | `POST /v1/memories` | [memories.doc.md](docs/memories.doc.md) |
| 4 | `POST /v1/memories/search` | [memories-search.doc.md](docs/memories-search.doc.md) |
| 5 | `POST /v1/context/compose` | [context-compose.doc.md](docs/context-compose.doc.md) |
| 6 | `PATCH /v1/memories/{memory_id}` | [memories-patch.doc.md](docs/memories-patch.doc.md) |
| 7 | `DELETE /v1/memories/{memory_id}` | [memories-delete.doc.md](docs/memories-delete.doc.md) |
| 8 | `GET /v1/deletions/{job_id}` | [deletions.doc.md](docs/deletions.doc.md) |
| 9 | `POST /v1/feedback` | [feedback.doc.md](docs/feedback.doc.md) |
| 10 | `GET /v1/traces/{trace_id}` | [traces.doc.md](docs/traces.doc.md) |

All ten in plain words: [apis-explained.doc.md](docs/apis-explained.doc.md).
A code trace per endpoint: [docs/flow/](docs/flow/).

Supporting endpoints the screens need: `POST /auth/signup`, `POST /auth/login`,
`GET`/`PATCH /v1/consent` (pause and opt-out), `GET /metrics`, `GET /policy`,
`GET /quality/runs`, `GET /subjects`, `GET /health`.

Errors use stable codes: `VALIDATION_FAILED`, `UNAUTHENTICATED`,
`SUBJECT_MISMATCH`, `CONSENT_DENIED`, `RATE_LIMITED`, `NOT_FOUND`, `CONFLICT`,
`SERVICE_UNAVAILABLE`, `UNSUPPORTED_SCHEMA_VERSION`.

## MCP tools - all 5 required (`abc.md` §5.4)

`search_memory`, `add_explicit_preference`, `correct_memory`, `delete_memory`,
`explain_memory_use`. The server is started for one subject, so no tool can
name another; each call goes through the API's own auth, consent, validation
and rate limit, and is audited. No generic graph query tool.

```bash
python -m memory.mcp_server user_001
```

Details: [mcp-tools.doc.md](docs/mcp-tools.doc.md).

---

## Logging in and privacy

- Passwords are stored only as a **salted scrypt hash** (`memory/accounts.py`).
- A **taken user id is refused**, so nobody can sign up as somebody else.
- **5 wrong passwords** pause logins for that id for 15 minutes; a wrong id and
  a wrong password get the same answer.
- A login gives a **15-minute pass** in an httpOnly cookie the page cannot read.
- **Every request is checked twice**: the pass is genuine and unexpired, and it
  belongs to the listener whose data is asked for (`SUBJECT_MISMATCH`
  otherwise). The web app writes the logged-in user's id into every request
  itself, so a page cannot ask for anybody else.
- Only an id, consent state, region and age band are kept about a person -
  never a name or email (`abc.md:53`, minimization).

---

## Data model

| Thing | Where |
|---|---|
| Event contract | `memory/models.py::Event`, frozen copy `data/schemas/event_v1.json` |
| Memory fact | `memory/graph.py` - `valid_from`, `valid_to`, `recorded_at`, `confidence`, `status`, `source_event_ids` |
| Vector | 384 numbers from the SentenceTransformers model `all-MiniLM-L6-v2` (run with fastembed, no PyTorch), on the memory node itself, property `embedding_384`, so deleting the memory deletes its vector |
| Context package | `memory/models.py::ContextPackage` |
| Memory types | `data/memory_types.yaml` (definition, example, counterexample) and `data/policy_registry.yaml` (sensitivity, retention, eligibility) |
| Retention by region and age | `data/retention_rules.yaml` |
| Entity catalog | `data/catalog.yaml` |
| Golden evaluation set | `data/golden-sets/pilot_golden_set.json` |
| PostgreSQL tables | `infrastructure/database-migrations/*.sql` |

```
(Memory)-[:ABOUT]->(Entity)
(Memory)-[:SUPERSEDES]->(Memory)     # a correction, keeping history
```

Every `Memory` node carries its `subject_id`, and every query filters on it.

---

## Environment variables

All in `.env.example`, already matching what `docker compose` starts.

| Variable | Meaning |
|---|---|
| `MEMORY_JWT_SECRET` | Signs the passes. A new long random value for any deployment; the frontend needs the same one |
| `GEMINI_API_KEY` | The model for extraction (endpoint 2 and the worker) |
| `GEMINI_MODEL` / `GEMINI_FALLBACK_MODEL` | Optional. Default `gemini-3.6-flash`, falling back to `gemini-2.5-flash` when the first one's free quota runs out |
| `DATABASE_URL` or `POSTGRES_*` | PostgreSQL |
| `REDIS_URL` or `REDIS_*` | Redis |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | Neo4j |
| `KAFKA_BOOTSTRAP` | The Kafka / Redpanda address (`localhost:19092` locally) |
| `KAFKA_USERNAME` / `KAFKA_PASSWORD` / `KAFKA_SASL_MECHANISM` | Only for a hosted Kafka; empty locally |
| `RUN_WORKER_IN_API` | `true` on a free host: run the worker inside the API instead of as its own service |
| `DEMO_PASSWORD` | Local testing only: gives `user_001` ... `user_005` this login password. Leave unset on a server |

---

## Deployment

| Part | Where | How |
|---|---|---|
Everything runs on free plans:

| Part | Where | Setting in Render |
|---|---|---|
| API **and worker** (one free web service) | Render - [render.yaml](render.yaml) | `RUN_WORKER_IN_API=true` runs the worker inside the API |
| PostgreSQL | Neon | `DATABASE_URL` |
| Redis | any free Redis | `REDIS_URL` (`rediss://` when it uses TLS) |
| Neo4j | Neo4j AuraDB | `NEO4J_URI`, `NEO4J_PASSWORD` (`NEO4J_USER` = `neo4j`) |
| Kafka | Redpanda Cloud - topics `interaction-events`, `interaction-events-dlq` | `KAFKA_BOOTSTRAP`, `KAFKA_USERNAME`, `KAFKA_PASSWORD` |
| Frontend | Netlify (or Vercel) - the frontend repo's `netlify.toml` | there: `MEMORY_API_BASE_URL` and the same `MEMORY_JWT_SECRET` |

Plus `GEMINI_API_KEY` and a new `MEMORY_JWT_SECRET` in Render.

Only settings differ between local and deployed - the code is the same.

- **No setup step:** when the API starts it creates the tables, Neo4j
  constraints and vector index if they are missing (`memory/startup.py`).
- **Fits the free 512 MB:** about 320 MB with the worker inside, because the
  embedding model runs without PyTorch (`memory/embeddings.py`).
- **Gemini busy?** A "503 high demand" answer is retried automatically
  (2, 5, then 10 seconds) before an event goes to the dead-letter queue.
- **No test-user logins on the server:** `DEMO_PASSWORD` is not set there,
  so the only accounts are people who sign up.
- **Free services sleep** when idle; the first request after a pause can
  take up to a minute.

---

## Tests and results

```bash
python -m pytest -q                    # 443 tests, real stores, model replaced
python scripts/verify_endpoints.py     # 62 checks, each citing its abc.md line
python scripts/run_golden_set.py       # 12 golden cases, feeds Quality review
```

| Check | Result |
|---|---|
| Test suite | 443 passed |
| Endpoint checks against the requirements | 62 / 62 |
| Golden set | 12 / 12 - precision at top 1.000, contradiction rate 0.000, provenance completeness 1.000 |
| Retrieval + composition latency | p95 about 70 ms locally, against the 250 ms budget (`abc.md:170`) - live on the Overview screen |

Run `verify_endpoints.py` with the worker stopped - it checks exact memory
versions, and a running worker strengthening the same memory changes them.

---

## Screenshots

Taken from the live site (https://spotifyfrontend11.netlify.app) with a fresh
demo account.

**Login** - sign up or log in; the page wakes the free backend while you type.

![Login](docs/screenshots/01-login.png)

**Context preview** - one message, *"play something romantic"*: the two memories
it learned earlier from *"I love Arijit Singh but no heavy metal"*, how they
ranked, the exact text the AI receives, and matching songs.

![Context preview](docs/screenshots/02-context-preview.png)

**Memory explorer** - everything remembered about this user.

![Memory explorer](docs/screenshots/03-memory-explorer.png)

**Correction & deletion** - pick a memory to correct, expire or delete.

![Correction and deletion](docs/screenshots/04-correction-and-deletion.png)

**Overview** - health, latency, ingestion, fallbacks, deletions. The live
latency is high because the free host sleeps and the cloud stores sit in other
regions; locally p95 is about 70 ms.

![Overview](docs/screenshots/05-overview.png)

**Schema & policy** - memory types with examples, retention, allowed fields.

![Schema and policy](docs/screenshots/06-schema-and-policy.png)

**Quality review** - golden-set results (run against the local stores; empty
on the live site until it is run there).

![Quality review](docs/screenshots/07-quality-review.png)

**Audit trace** - why one answer used the memories it did, with no memory text.

![Audit trace](docs/screenshots/08-audit-trace.png)

---

## Limitations - stated honestly

1. **No LLM answer is generated.** `POST /v1/context/compose` returns the
   package an AI orchestrator would consume; the orchestrator is outside this
   repository. The songs on Context preview are a labelled demo (iTunes
   previews), not part of the memory system.
2. **Login is a pilot stand-in for Spotify's.** `abc.md` assumes the gateway
   verifies an existing Spotify session; here `POST /auth/signup` and
   `POST /auth/login` do that job. Services still use a token minted with the
   shared secret (`scripts/make_token.py`).
3. **Extraction depends on Gemini.** Without a key, endpoint 2 returns a clean
   503. When Gemini is overloaded, events go to the dead-letter queue -
   `python scripts/replay_dead_letters.py` retries them.
4. **Surface policy is a filter, not a score.** `abc.md` §5.4 lists seven
   ranking signals. Six are scored (`memory/retrieval.py`); the seventh,
   surface policy, removes a memory outright when the surface may not use it,
   because a memory the policy forbids must never reach the AI however well it
   scores.
5. **Retention numbers are our choice.** `abc.md` requires retention by type,
   region and age but names no values; they are in `data/*.yaml`.

---

## Team contribution

Single-contributor pilot build.

---

## Repository layout

```
memory/                    the API, worker logic, login, policy and MCP server
scripts/                   setup, worker, tokens, checks, replay, housekeeping
tests/                     pytest suites against the real stores
data/                      policy registry, memory types, retention rules,
                           catalog, golden set, event schema
infrastructure/            PostgreSQL migrations
docs/                      one doc per API, code flows, requirements
abc.md                     the requirements, as text (cited as abc.md:<line>)
render.yaml                Render deployment
docker-compose.yml         the four stores, locally
start-backend.cmd          one-command start on Windows
```
