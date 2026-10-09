# Running this project

Clone it, run four commands, open two terminals.

---

## Setup — four commands, once

```bash
git clone https://github.com/omsemwal/spotify-personalized-ai.git
cd spotify-personalized-ai

pip install -r requirements.txt
cp .env.example .env
docker compose up -d
python scripts/setup.py
```

`docker compose up -d` starts all four stores: PostgreSQL, Redis, Neo4j and
Redpanda. `setup.py` waits for them, creates the tables, the graph
constraints and the vector index, and seeds the demo subjects.

**One thing to edit.** Open `.env` and put a Gemini key on this line:

```
GEMINI_API_KEY=replace-me
```

Free from **aistudio.google.com** -> Get API key. Everything else in `.env`
already matches what `docker compose` starts.

Without a key the system still runs - endpoint 2 returns a clean 503, and
the other nine work normally.

---

## Running it — one command on Windows

```bash
./start-backend.cmd
```

Starts the databases, stops any worker left from a previous run, starts the
worker, then the API on http://127.0.0.1:8000. Ctrl+C stops it. The frontend
has its own `start-frontend.cmd` in its repository.

The same, by hand, is two terminals:

## Running it — two terminals

### Terminal 1 — the API

```bash
python -m uvicorn memory.api:app --reload --port 8000
```

```
INFO:     Uvicorn running on http://127.0.0.1:8000
```

Open **http://127.0.0.1:8000/docs** - every endpoint, with a Try it out
button.

### Terminal 2 — the worker

```bash
python scripts/run_processor.py --forever
```

```
processor running, Ctrl+C to stop
handled 1, failed 0, memories stored 2
```

**This one matters.** It reads the queue and turns events into memories.
Without it, events are captured and nothing ever appears in the graph -
which looks exactly like a bug.

---

## Why two terminals

That split is the whole architecture, not an inconvenience.

```
Terminal 1   the API      answers in milliseconds, never waits for a model
Terminal 2   the worker   does the slow work afterwards
```

`abc.md:324` - *"Keep the user path independent of downstream graph-write
latency."* A listener asking for music must never wait while we call a
model and write a graph. So the API accepts the event, replies, and the
worker picks it up from the queue.

If they ran in one process, a slow model call would make the listener wait.

---

## Checking it works

```bash
python -m pytest -q                    # 443 tests, about 2 minutes
python scripts/verify_endpoints.py     # all 10 endpoints vs the requirements
```

The second is the better one to watch: 62 checks, each naming the `abc.md`
line it comes from. Run it with the worker stopped - it checks exact memory
versions, and a running worker strengthening the same memory changes them.

Both clear the test users' data (`user_001` ... `user_005`) as they go.

---

## On the machine this was built on

The old project's containers already hold ports 5432, 6379, 7687 and 19092,
so `docker compose up -d` will refuse to start. Use the containers that are
already there instead:

```bash
docker start memory_system_redis memory_system_neo4j memory_system_redpanda
```

PostgreSQL runs as a Windows service and starts on its own. `.env` already
points at all of them.

---

## Trying it by hand

**Get a token** (they last 15 minutes):

```bash
python scripts/make_token.py user_001
```

Copy the whole `Authorization: Bearer ...` line into Postman, or into the
**Authorize** button at the top of `/docs`.

**Send an event:**

```bash
curl -X POST http://127.0.0.1:8000/v1/events \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"schema_version":"1.0","subject_id":"user_001",
       "event_type":"ai_interaction","surface":"chat","locale":"en-US",
       "occurred_at":"2026-09-25T10:00:00Z","consent_state":"granted",
       "source_event_id":"src_1","idempotency_key":"try_1",
       "content":"I do not want country music"}'
```

Watch the worker terminal — a memory appears a moment later.

**Then ask what it remembers:**

```bash
curl -X POST http://127.0.0.1:8000/v1/context/compose \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"subject_id":"user_001","intent":"put some music on",
       "surface":"player","token_budget":400}'
```

---

## The MCP server - the five tools for a model

```bash
python -m memory.mcp_server user_001
```

It speaks MCP over stdio, so an MCP client starts it. It is bound to the one
subject given, and calls the API in-process - the API does not need to be
running, the stores do. See `docs/mcp-tools.doc.md`.

---

## On this machine: which Neo4j

Two old projects also have a Neo4j container. Only `memory_system_neo4j`
matches `.env`. If the worker logs `Neo.ClientError.Security.Unauthorized`,
another one has taken port 7687:

```bash
docker stop memory-neo4j
docker start memory_system_neo4j
```

---

## Housekeeping

```bash
python scripts/cleanup_expired.py      # delete raw events past 30 days
python scripts/expire_memories.py      # mark memories past their retention
```

In production both belong on a schedule.

---

## When something is wrong

| Symptom | Cause | Fix |
|---|---|---|
| `401 UNAUTHENTICATED` | The token expired — they last 15 minutes | In the web app, log in again; by hand, `python scripts/make_token.py user_001` |
| `429` on login | 5 wrong passwords for that user id | Wait 15 minutes |
| `403 CONSENT_DENIED` | No consent row for that subject | Use `user_001`, `user_002` or `user_003` |
| `503 SERVICE_UNAVAILABLE` | Gemini is busy, or the key is missing | Wait a minute, or check `GEMINI_API_KEY` in `.env` |
| `429 RATE_LIMITED` | More than 120 requests in a minute | Wait for the next minute |
| Redis errors | Docker is not running | Start Docker Desktop, then `docker start memory_system_redis` |
| Events captured but no memories appear | The worker is not running | `python scripts/run_processor.py --forever` |
| Still no memory, worker running | Gemini was busy (`503 ... high demand`); the event went to the dead-letter queue | Wait a minute, then `python scripts/replay_dead_letters.py` |
| Two workers / old behaviour after a restart | A worker from before is still running | Restart with `./start-backend.cmd` - it stops the old one first |
| `Neo.ClientError.Security.Unauthorized` | Another Neo4j container holds port 7687 | `docker stop memory-neo4j` then `docker start memory_system_neo4j` |
| `KafkaTimeoutError` | Wrong port — 9092 is internal to Docker | `.env` should say `KAFKA_BOOTSTRAP=localhost:19092` |

---

## The test subjects

Seeded by the migration, so there is always something to try. To log in to
the web app as them, set `DEMO_PASSWORD` in your private `.env` and run
`python scripts/setup.py` - they get that password. Anyone else signs up with
their own user id.

| Subject | Consent | Use for |
|---|---|---|
| `user_001` | granted | Normal testing |
| `user_002` | granted | Checking one subject cannot see another's memories |
| `user_003` | granted | Memory-disabled experiment arm - always answered without memory |
| `user_004` | **denied** | Checking consent is enforced |
| `user_005` | **paused** | Checking the no-memory fallback |

---

## Looking inside

**Neo4j** — http://localhost:7474, log in `neo4j` / `neo4j_password_secure`:

```cypher
MATCH (m:Memory {subject_id: 'user_001'}) RETURN m
MATCH (m:Memory)-[:ABOUT]->(e:Entity) RETURN m, e
```

**PostgreSQL:**

```bash
psql -U postgres -d spotify_personalized_ai -c "SELECT * FROM audit_log ORDER BY id DESC LIMIT 10"
```

**Redpanda:**

```bash
docker exec memory_system_redpanda rpk topic list
```

---

## Where to read more

| | |
|---|---|
| `docs/apis-explained.doc.md` | All ten APIs in plain words |
| `docs/flow/` | A code trace per API — function by function |
| `docs/REQUIREMENTS.md` | What the project has to deliver |
