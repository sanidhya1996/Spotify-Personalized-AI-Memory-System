# MCP tools — `memory/mcp_server.py`

The five tools a model is allowed to use on a listener's memory.

> `abc.md` §5.4 — *"MCP and Tool Interface"*: exactly five tools,
> `search_memory`, `add_explicit_preference`, `correct_memory`,
> `delete_memory`, `explain_memory_use`, with typed input and output
> schemas, authenticated subject binding, authorization checks, rate limits
> and audit events. *"Never expose a generic graph query tool to the model."*

---

## 1. What it is for

The ten endpoints are for Spotify's services. MCP is for the **model**: a
standard way to give it a small set of named, typed tools instead of raw
database access. The model can search, add, correct, delete and explain —
and nothing else. It cannot write a graph query, so it cannot wander into
another listener's data or dump the store.

---

## 2. How to run it

```bash
python -m memory.mcp_server user_001
```

It speaks MCP over stdio, which is how an MCP client (a desktop AI app, an
agent framework) normally starts a server. The API does not need to be
running separately: the tools call it in-process. The stores (Postgres,
Redis, Neo4j) do need to be up.

---

## 3. The five tools

| Tool | Input | What it calls | Returns |
|---|---|---|---|
| `search_memory` | `intent`, `surface`, `locale`, `limit` | `POST /v1/memories/search` | ranked memories + `trace_id` |
| `add_explicit_preference` | `fact`, `entities` | `POST /v1/memories` as `explicit_preference` | `memory_id`, graph version, policy state |
| `correct_memory` | `memory_id`, `fact`, `entities` | `PATCH /v1/memories/{id}` operation `correct` | new `memory_id` + the one it `superseded` |
| `delete_memory` | `memory_id` | `DELETE /v1/memories/{id}` | `job_id` to follow with `GET /v1/deletions/{job_id}` |
| `explain_memory_use` | `memory_id`, `trace_id` | `GET /v1/traces/{trace_id}` | the trace decisions for that one memory, redacted |

The types come from the Python signatures; the MCP SDK turns them into the
JSON schemas the model sees.

---

## 4. Step by step — what happens on one tool call

1. **The server was started for one subject.** `bind("user_001")` runs once
   at start-up. No tool has a `subject_id` input, so the model has no way
   to name anybody else.
2. **The tool builds the request** for the matching endpoint, with the bound
   subject filled in.
3. **`_call()` mints a token** for that subject, service id
   `memory-mcp-server`, and calls the API in-process.
4. **The API does its usual checks** — token, subject binding, consent,
   validation, rate limit (120 a minute). Nothing is re-implemented in the
   MCP layer, so the model gets exactly the protection every other caller
   gets.
5. **`_call()` writes one audit row**: action `mcp.<tool>`, the outcome, the
   correlation id and the memory id. Never the memory text.
6. **The tool returns the result**, or `{"error": {"code": ..., "message": ...}}`
   with the API's stable code — for example `CONSENT_DENIED` or
   `NOT_FOUND`. The model gets a clear answer, not a stack trace.

`correct_memory` reads the memory's current version first and sends it as
`expected_version`, because the endpoint requires one.

---

## 5. What the tests check — `tests/test_mcp_server.py`

- exactly the five tools are listed, no more
- no tool takes a `subject_id`
- add, then search finds it by meaning
- a correction supersedes and keeps the old memory as history
- delete returns a job id
- explain returns only that memory's decisions
- a server bound to `user_002` cannot search, correct or delete `user_001`'s memory
- every call is audited
- a denied subject gets `CONSENT_DENIED`, not an exception
