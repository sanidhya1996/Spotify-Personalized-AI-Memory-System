# `GET /v1/traces/{trace_id}`

Endpoint 10 of 10. Explains why the system did what it did.

> `abc.md:320` — *"Return authorized retrieval and policy decisions with
> sensitive fields redacted."*

---

## 1. What it is for

Somebody complains: *"why did it suggest that?"*

Without a record, the honest answer is a shrug. `abc.md:101` makes
answering it a product requirement:

> *"Replays the retrieval trace, shows candidate scores and policy
> decisions, and links the output to source memories."*

So every search and every composition writes down what it decided, and
this reads it back.

---

## 2. The tension this endpoint has to hold

It has two requirements that pull against each other.

**It must be useful.** A reviewer needs to see which memories were
considered, which were used, which were dropped, and why. A trace that
cannot explain a decision is not worth keeping.

**It must be redacted.** `abc.md:320` says *"sensitive fields redacted"*,
and `abc.md:322` says logs carry *"identifiers and outcomes, not raw
private content"*. Support staff reading a complaint should not end up
reading somebody's private memories.

**The resolution: identifiers, scores and reasons — never memory text.**

A reviewer sees *that* `mem_af02...` was included with a score of 0.831
because it was the strongest explicit preference. They do not see that it
says *"prefers instrumental music while working"*. If they need that, they
look the memory up through a path that checks their authorisation.

---

## 3. The request

```
GET /v1/traces/cid_572507ca91c54483?subject_id=user_001
Authorization: Bearer <token>
```

The trace id is the correlation id every response already carries in its
`X-Correlation-Id` header, so a caller always has it.

---

## 4. The response

```json
{
  "trace_id": "cid_572507ca91c54483",
  "decisions": [
    {"stage": "ranking", "decision": "included",
     "memory_id": "mem_bc4f0cd179704825",
     "reason": "strongest signal: explicitness", "score": 0.831,
     "recorded_at": "2026-09-25T12:00:00Z"},

    {"stage": "policy", "decision": "excluded",
     "memory_id": "mem_4ca8190641844d65",
     "reason": "candidate_preference not allowed on player",
     "score": null, "recorded_at": "2026-09-25T12:00:00Z"}
  ],
  "actions": [
    {"action": "search.completed", "outcome": "found",
     "memory_id": null, "recorded_at": "..."}
  ],
  "redacted": true
}
```

Note what is **not** there: no `fact`, no listener's words, nothing they
typed.

**Errors**

| Status | Code | When |
|---|---|---|
| 401 | `UNAUTHENTICATED` | Token missing, forged, or expired |
| 403 | `SUBJECT_MISMATCH` | Token belongs to a different subject |
| 404 | `NOT_FOUND` | No such trace **for this subject** |

---

## 5. What gets recorded

Four stages, matching the pipeline `abc.md:142` describes:

| Stage | Records |
|---|---|
| `retrieval` | Which memories were considered |
| `ranking` | Which were included, and their score |
| `policy` | Which were dropped, and why |
| `composition` | What went into the final package |

Plus the service actions from the audit trail — `abc.md:345` wants the
trace to show *"tool calls, service decisions, memory identifiers,
timestamps, and redacted outcomes"*, not only retrieval.

**Decisions are written in the background**, so recording them never slows
the request that made them.

---

## 6. Why an exclusion is as important as an inclusion

A trace that only shows what was used cannot explain an **absence** — and
"why didn't it know that?" is the more common complaint.

So every drop is recorded with its reason:

```
"candidate_preference not allowed on player"
"confidence 0.2 below 0.35"
"would exceed the 400 token budget"
"more than 2 about topic_jazz"
```

Each of those is a different bug to chase if it turns out to be wrong.

---

## 6b. Call flow

See `docs/flow/api-10-traces.flow.md`.

---

## 7. The functions this API uses

| Function | File | Why it exists |
|---|---|---|
| `get_trace()` | `memory/api.py` | The endpoint: checks access, reads decisions and actions |
| `record_decision()` | `memory/trace.py` | Writes one decision — identifiers only |
| `record_search()` | `memory/trace.py` | Writes a whole request's decisions in one go |
| `get_trace()` | `memory/trace.py` | Reads the decisions back, subject-scoped |
| `get_audit_for_trace()` | `memory/trace.py` | Reads the service actions that share this trace id |
| `delete_traces()` | `memory/trace.py` | Removes a subject's traces, for deletion and tests |

**Reused**

| Function | Why it is used here |
|---|---|
| `errors.add_correlation_id()` | The trace id is the correlation id |
| `auth.bind_subject()` | The same cross-subject check as every endpoint |

---

## 8. Tests

`tests/test_feedback_trace_api.py` — the trace half.

Both halves of the tension are tested, because a trace that is safe but
useless fails as badly as one that leaks:

**Useful**
- A search leaves a **replayable** trace
- The trace says **which memories were used**
- It carries the **scores**
- It says **why something was dropped**
- It shows what the **services** did

**Redacted**
- The trace holds **no memory text** — a memory with a distinctive phrase
  is stored, and the phrase does not appear anywhere in the trace
- It still **names the memory**, so a reviewer can look it up properly

---

## 9. Done

| Requirement | Line |
|---|---|
| Return retrieval and policy decisions | `abc.md:320` |
| Sensitive fields redacted | `abc.md:320`, `:322` |
| Authorized — subject-scoped | `abc.md:320`, `:119` |
| Replay the retrieval trace with candidate scores | `abc.md:101` |
| Link the output to source memories | `abc.md:101` |
| Show service decisions and timestamps | `abc.md:345` |
| Every response links to a trace | `abc.md:322` |

---

## 10. Pending

| Missing | Requirement |
|---|---|
| **Only retrieval and composition are traced** — `abc.md:142` also wants event intake, transformation, graph write and embedding write traced. Those write audit lines but not trace decisions | `abc.md:142` |
| **No listing** — you can read one trace by id, but not "show me this subject's recent traces" | `abc.md:345` |
| **No role check** — any token for the subject can read their traces. `abc.md:343` wants a read-only view for most roles, which needs roles to exist | `abc.md:343` |
| **Traces never expire** — they accumulate with no retention rule of their own | `abc.md:139` |
| **Not tied to MCP tool calls** — `abc.md:345` mentions tool calls, and the MCP server is not built | `abc.md:127` |
