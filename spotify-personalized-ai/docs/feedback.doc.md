# `POST /v1/feedback`

Endpoint 9 of 10. Records what a listener thought of a memory.

> `abc.md:318` — *"Record relevance, correction, rejection, or experience
> feedback **without self-validating model output**."*

---

## 1. What it is for

A listener sees a suggestion and reacts: that was helpful, that was
irrelevant, that is just wrong.

Recording it is easy. **The requirement is about what we do with it.**

---

## 2. The trap this endpoint exists to avoid

Picture the loop:

```
1. The model guesses:  "might enjoy ambient music"
2. The guess shapes a reply
3. The listener likes the reply and clicks thumbs-up
4. The guess gets more confident
5. It is now shown more often
6. More thumbs-up...
```

By step 6 the system is **certain** about something nobody ever said. Not
one new fact arrived. It used its own output as evidence for its own
output.

`abc.md:149` — *"Capture user corrections and reviewer decisions **without
automatically reinforcing model-generated claims**."*

---

## 3. The rule, and why it is asymmetric

| Feedback | On a memory they **stated** | On something **we inferred** |
|---|---|---|
| **helpful** | ✅ counts | ❌ **recorded, but counts for nothing** |
| **unhelpful** | ✅ counts | ✅ counts |
| **wrong** | ✅ counts | ✅ counts |

**Negative feedback always counts.** Being told we are wrong is
information from the listener, whatever produced the memory.

**Positive feedback counts only for their own words.** Agreeing with
something they said is real evidence. Agreeing with our guess is not.

**Why the asymmetry is right, not merely cautious.** `abc.md:57` — *"one
wrong memory can be more damaging than three missing ones."* Failing to
strengthen a correct guess costs a little accuracy. Strengthening a wrong
one compounds, and gets louder every time it is shown.

| Memory type | Reinforceable by a thumbs-up? |
|---|---|
| `explicit_preference` | ✅ they said it |
| `exclusion` | ✅ they said it |
| `correction` | ✅ they said it |
| `candidate_preference` | ❌ we guessed it |
| `episode` | ❌ we observed it |

---

## 4. The request

```json
{
  "subject_id": "user_001",
  "kind": "relevance",
  "sentiment": "unhelpful",
  "memory_id": "mem_af02dce95af44517",
  "trace_id": "cid_572507ca91c54483"
}
```

**`kind`** — the four `abc.md:318` names: `relevance`, `correction`,
`rejection`, `experience`.

**`memory_id`** is optional. Feedback about the whole experience is not
about one memory.

**`trace_id`** ties the feedback back to the decisions that produced the
reply — `abc.md:194` asks the orchestration layer to record feedback
links.

---

## 5. The response

```json
{
  "feedback_id": "fbk_a1b2c3d4e5f6",
  "recorded": true,
  "reinforced": false,
  "reinforce_reason": "candidate_preference was inferred by us; positive
                       feedback on our own output is not evidence for it"
}
```

`recorded` and `reinforced` are **separate on purpose**. Feedback is
always kept — the listener's reaction is real data. Whether it was allowed
to change anything is a different question, and the answer is stated
rather than left implicit.

`reinforce_reason` is stored too, so a reviewer months later can see why a
particular piece of feedback did or did not count.

**Errors**

| Status | Code | When |
|---|---|---|
| 401 | `UNAUTHENTICATED` | Token missing, forged, or expired |
| 403 | `SUBJECT_MISMATCH` | Token belongs to a different subject |
| 404 | `NOT_FOUND` | The named memory is not this subject's |
| 422 | `VALIDATION_FAILED` | Unknown kind or sentiment |
| 429 | `RATE_LIMITED` | More than 120 requests this minute |

---

## 6. Where negative feedback goes

It is written to the audit trail as `feedback.negative`, which is where
retrieval reads it from.

`abc.md:124` lists negative feedback among the seven rerank signals — so a
memory the listener rejected scores lower next time, without being
deleted. They said it was unhelpful, not that it was untrue.

---

## 6b. Call flow

See `docs/flow/api-9-feedback.flow.md`.

---

## 7. The functions this API uses

| Function | File | Why it exists |
|---|---|---|
| `create_feedback()` | `memory/api.py` | The endpoint: checks access, then records |
| `record()` | `memory/feedback.py` | Stores the feedback and applies it only where allowed |
| `may_reinforce()` | `memory/feedback.py` | The rule — decides whether this feedback may count |
| `list_for_subject()` | `memory/feedback.py` | Reads one subject's feedback back |
| `new_feedback_id()` | `memory/feedback.py` | Makes the identifier |

**Reused**

| Function | Why it is used here |
|---|---|
| `graph.get_memory()` | Confirms the memory is this subject's, and reads its type |
| `auth.bind_subject()` | The same cross-subject check as every endpoint |
| `db.record_audit()` | Negative feedback becomes a ranking signal |

---

## 8. Tests

`tests/test_feedback_trace_api.py` — the feedback half.

Most of them exist to prove the loop cannot close:

- A thumbs-up on **our own guess** does not count
- A thumbs-up on an **episode** does not count either
- A thumbs-up on **their own words** does count
- **Every** inferred type is unreinforceable; **every** stated type is reinforceable
- Being told we are **wrong always counts**, whatever produced the memory
- Negative feedback **reaches the ranking signal**

---

## 9. Done

| Requirement | Line |
|---|---|
| Record relevance, correction, rejection, experience feedback | `abc.md:318` |
| Without self-validating model output | `abc.md:318`, `:149` |
| Negative feedback as a rerank signal | `abc.md:124` |
| Feedback linked to the trace that caused it | `abc.md:194` |
| Subject isolation | `abc.md:119` |
| Audit event | `abc.md:460` |

---

## 10. Pending

| Missing | Requirement |
|---|---|
| **Reinforcement is recorded, not applied** — `reinforced: true` is stored, but nothing yet raises a memory's confidence from it. The decision is made and kept; acting on it is not built | `abc.md:149` |
| **No reviewer queue** — `abc.md:149` also covers "reviewer decisions", and `abc.md:71` wants a review panel | `abc.md:71`, `:149` |
| **No correction shortcut** — `kind: "correction"` records that something is wrong but does not create a correction memory. That is endpoint 6 | `abc.md:318` |
| **Feedback is not measured** — `abc.md:59` lists correction rate and satisfaction as online success metrics | `abc.md:59` |
| **No golden set** for feedback handling | `abc.md:148` |
