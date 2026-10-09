# Audit trace — screen document

**Screen 7 of 7** · **Route:** `/trace` · **File:** `apps/memory-console/app/trace/page.tsx`

**Requirement — `abc.md:345`:** *"Audit trace: Authorized view of tool calls,
service decisions, memory identifiers, timestamps, and redacted outcomes."*

---

## Call flow

1. **`load(id)`** → **`get("/v1/traces/{trace_id}?subject_id=…")`** — `lib/api.ts`
   The subject goes in the query string here, not a body: a `GET` has none.
   → **Backend `GET /v1/traces/{trace_id}`** →
   - **`trace_service.get_trace()`** in `memory/trace.py` → **PostgreSQL**, the
     per-stage decisions
   - **`trace_service.get_audit_for_trace()`** → **PostgreSQL** `audit_log`, the
     audited service actions

2. **`makeOne()`** → **`post("/v1/context/compose", …)`**, then `load()` with the
   trace id that comes back. Without this an operator would have to copy an id
   from another screen before this one could show anything at all.

3. **`setTrace()`** — renders the counts, the service decisions, and the audited
   actions.

---

## Why an operator is allowed to read this

Because it contains no memory text. `abc.md:320` asks for *"authorized retrieval
and policy decisions with sensitive fields redacted"*, and the backend's
`memory/trace.py` stores identifiers, stages, decisions, scores and reasons only.

The screen states that with a `redacted — carries no memory text` badge taken from
the response's own `redacted` field, rather than asserting it itself.

Verified live: no decision in a returned trace carries a `fact`.

---

## What is shown, against the requirement

| Asked for | Shown |
|---|---|
| Service decisions | Yes — one row per decision, coloured by stage: ranking, policy, composition |
| Memory identifiers | Yes — on every decision that concerns one |
| Timestamps | Yes — `recorded_at` on every decision |
| Redacted outcomes | Yes — the decision itself (`included`, `excluded`), the score, the reason, and the redaction stated |
| Tool calls | Yes — the **Audited actions** card: what each service did under this correlation id, with its outcome |

The requirement calls these "tool calls" because `abc.md:127` expects MCP tools to
be the access path. The MCP server is not built, so what is audited today is the
service actions behind the same endpoints. The card shows what the audit trail
actually holds, rather than a heading with nothing under it.

---

## Functions

| Function | File | Why it exists |
|---|---|---|
| `AuditTracePage()` | `app/trace/page.tsx` | The screen; holds the trace id and the loaded record. |
| `load()` | same | Looks up one trace by id. |
| `makeOne()` | same | Composes a context package so there is a trace to look at, then loads it. |
| `stageTone()` | same | Colours a decision by pipeline stage, so the shape of a trace reads before any of it does. |
| `decisionTone()` | same | Green for kept, red for dropped. |
| `get()`, `post()` | `lib/api.ts` | The two calls this screen makes. |

---

## Where a trace id comes from

Any response carries one. The **Context preview** screen prints it, the search
endpoint returns it, and **every error body** carries it as `correlation_id` —
which is the same identifier. That is why a failed request on any screen is worth
bringing here: the id in the red box is the id this screen accepts.
