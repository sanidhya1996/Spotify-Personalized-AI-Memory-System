# `POST /v1/memories/search`

Endpoint 4 of 10. Finds the memories that matter for what is being asked
now.

> `abc.md:309` — *"Return ranked subject-scoped memories for intent,
> surface, locale, and token budget."*

---

## 1. What it is for

APIs 1–3 remember. This is the first one that **reads memories back**.

Given what a listener is asking for right now, it returns the handful of
memories worth using — ranked, filtered, and with the reason each one
scored as it did.

The standard it is held to is precision, not recall. `abc.md:57`, the
Quality Engineering Lead:

> *"We need precision at the top of the retrieved set, not just retrieval
> recall. **One wrong memory can be more damaging than three missing
> ones.**"*

---

## 2. The request

```json
{
  "subject_id": "user_001",
  "intent": "something for concentrating while I work",
  "surface": "chat",
  "locale": "en-US",
  "limit": 10
}
```

`surface` matters: the same memory may be allowed in chat and forbidden on
the player.

---

## 3. The response

```json
{
  "results": [
    {
      "memory_id": "mem_af02dce95af44517",
      "memory_type": "explicit_preference",
      "fact": "Prefers instrumental music while working",
      "confidence": 0.9,
      "score": 0.831,
      "signals": {
        "intent_fit": 0.71,
        "explicitness": 1.0,
        "confidence": 0.9,
        "recency": 1.0,
        "repetition": 0.33,
        "negative_feedback": 1.0
      },
      "entities": ["topic_instrumental", "activity_working"],
      "evidence_count": 1
    }
  ],
  "removed": ["mem_xyz: candidate_preference not allowed on player"],
  "considered": 5,
  "trace_id": "cid_572507ca91c54483"
}
```

Three things here are deliberate:

- **`signals`** — not just the total. A score with no breakdown cannot be
  debugged when it ranks something wrongly.
- **`removed`** — what was found and then dropped, with the reason.
  `abc.md:192` says the policy engine removes items; saying which makes
  that checkable rather than invisible.
- **`trace_id`** — `abc.md:322` requires every response to link to a
  trace.

---

## 4. Finding: two searches, not one

> `abc.md:123` — *"Use **hybrid** candidate generation: graph traversal
> for relational relevance and vector similarity for semantic relevance."*

Finding memories is two different problems needing two different tools.

**Vector search is good at meaning.** *"music with no vocals"* finds
*"prefers instrumental"* with no shared words. It is poor at precision —
it will return the closest thing it has even when barely related.

**Graph traversal is the opposite.** It finds memories attached to an
entity the listener actually named, and misses anything worded
differently.

So we run both and merge on memory id. A memory found by both is usually
the strongest result; one found by only the graph is precise; one found by
only the vector is a good guess.

Both halves are bounded to 50 candidates — `abc.md:191`, *"candidate
generation is subject-scoped and bounded."*

---

## 5. Ranking: seven signals

> `abc.md:124` — *"Rerank by current intent, explicitness, confidence,
> recency, repetition, surface policy, and negative feedback."*

| Signal | Weight | Means |
|---|---|---|
| Intent fit | 0.30 | How close in meaning to what was asked |
| Explicitness | 0.25 | Said outright, or guessed by us |
| Confidence | 0.15 | How sure we were when we stored it |
| Recency | 0.15 | How long ago, fading over a year |
| Repetition | 0.10 | How many separate times it was said |
| Negative feedback | 0.05 | Marked unhelpful before |

Surface policy is the seventh, and it is not a weight — it is a filter.
See section 6.

> **The weights are ours.** `abc.md` names the signals but gives no
> numbers. They are gathered in one dictionary in `memory/retrieval.py`,
> so tuning them is one edit.

### Why reranking, and not just similarity

Real output from the running system:

```
0.831  [explicit_preference ] Prefers instrumental music while working
         intent=0.71  explicit=1.00

0.690  [episode             ] Played a focus playlist this morning
         intent=0.65  explicit=0.50
```

The episode is a *closer* match to the query than two of the entries above
it. It still ranks lower, because a thing the listener **said** outweighs a
thing they **did once**.

That is the whole point of reranking: closest in meaning is not the same
as most worth saying.

### Explicitness by type

| Type | Explicitness |
|---|---|
| `exclusion`, `correction`, `explicit_preference` | 1.0 |
| `episode` | 0.5 |
| `candidate_preference` | 0.3 |

---

## 6. Filtering: what never comes back

> `abc.md:192` — *"The policy engine removes prohibited, expired,
> contradicted, or over-budget items."*

| Dropped | Why |
|---|---|
| **Off-surface** | A `candidate_preference` is chat-only — an unconfirmed guess must not silently steer playback |
| **Superseded** | Contradicted by something newer; only `status: active` is searched |
| **Expired** | Past its retention date |
| **Another subject's** | Never reachable — the subject is in the query itself |
| **Over-diverse** | See below |

Real output, the same memories searched on two surfaces:

```
surface: chat     →  5 results
surface: player   →  3 results
   REMOVED: episode not allowed on player
   REMOVED: candidate_preference not allowed on player
```

### Diversity

> `abc.md:125` — *"Apply diversity and context-budget limits so one
> preference or content cluster does not dominate the context pack."*

At most **2 memories per entity**. Ten memories about the same artist is
not ten useful memories — it is one fact, crowding out everything else.

---

## 7. Subject isolation

`abc.md:119`. The subject is in the query itself, in both halves:

```cypher
MATCH (m:Memory {subject_id: $subject_id, status: 'active'})-[:ABOUT]->(e:Entity)
```

and in the vector search, where the index is searched and then filtered to
the subject before anything is returned.

Consent is re-checked here too. `abc.md:53` — *"must be enforced **before
memory reaches retrieval**"* — and this is that point. A listener who
paused memory since the fact was stored gets nothing back.

---

## 7b. Call flow — what actually runs, in order

```
POST /v1/memories/search
  │
  ├─1 errors.add_correlation_id()          memory/errors.py
  ├─2 auth.authenticate()                  memory/auth.py        -> 401
  ├─3 SearchRequest validation             memory/models.py      -> 422
  │
  └─4 api.search_memories()                memory/api.py
        │
        ├─ auth.bind_subject()             memory/auth.py        -> 403
        ├─ cache.is_rate_limited()  ──────────────► REDIS        -> 429
        ├─ db.get_consent()         ──────────────► POSTGRES     -> 403
        │
        ├─ db.negative_feedback()          memory/db.py
        │    memories marked unhelpful ────────────► POSTGRES
        │
        └─ retrieval.search()              memory/retrieval.py
              │
              ├─ retrieval.graph_candidates()
              │     │  memories about an entity the listener named
              │     ├─ entities.resolve_all() ──► data/catalog.yaml
              │     └─                        ──► NEO4J
              │
              ├─ retrieval.vector_candidates()
              │     │  memories that mean something similar
              │     └─ embeddings.search()    memory/embeddings.py
              │           ├─ embeddings.embed() ──► SentenceTransformers
              │           └─                     ──► NEO4J (vector index)
              │
              ├─ graph.get_memory()  ──────────────► NEO4J
              │     fills in details for vector-only hits
              │
              ├─ for each: retrieval.rank_one()
              │     │  combines the six signals into one score
              │     ├─ retrieval.recency_score()
              │     └─ retrieval.repetition_score()
              │
              ├─ sort by score, highest first
              │
              ├─ retrieval.allowed_on_surface()
              │     └─ policy.may_surface() ──► data/policy_registry.yaml
              │           drops what this surface may not show
              │
              └─ retrieval.apply_diversity()
                    stops one entity filling the results

        └─ [background] db.record_audit()  ──────────► POSTGRES

  response: {"results": [...], "removed": [...],
             "considered": 5, "trace_id": "cid_..."}
```

**Both searches run, then merge.** Neither alone is enough: the vector
half finds meaning, the graph half finds precision.

**Filtering happens after ranking**, so `removed` can explain what was
found and then dropped, rather than silently never looking.

---

## 8. Files

| File | Job |
|---|---|
| `memory/retrieval.py` | Candidates, ranking, diversity |
| `memory/embeddings.py` | The semantic half |
| `memory/graph.py` | The relational half |
| `memory/policy.py` | Which surfaces allow which types |
| `tests/test_search_api.py` | 22 tests |

---

## 8b. The functions this API uses

Names and why, not code.

**`memory/api.py`**

| Function | Why it exists |
|---|---|
| `search_memories()` | The endpoint: checks access and consent, then hands off to retrieval |

**`memory/retrieval.py`** - all the logic for this API

| Function | Why it exists |
|---|---|
| `graph_candidates()` | Finds memories attached to an entity the listener actually named |
| `vector_candidates()` | Finds memories that mean something similar, even with no shared words |
| `recency_score()` | Newer memories count for more, fading to zero over a year |
| `repetition_score()` | Something said three times is stronger than something said once |
| `rank_one()` | Combines the six weighted signals into one score, and keeps the breakdown |
| `allowed_on_surface()` | Drops memories this surface may not show, with the reason |
| `apply_diversity()` | Stops one entity filling the results - ten memories about one artist is one fact |
| `search()` | Runs both searches, merges, ranks, filters, returns |

**Reused from earlier APIs**

| Function | Why it is used here |
|---|---|
| `embeddings.search()` | The semantic half of the hybrid |
| `graph.get_memory()` | Fills in details for anything the vector search found |
| `policy.may_surface()` | The surface rule each memory type carries |
| `db.negative_feedback()` | Memory ids the listener marked unhelpful |
| `auth.bind_subject()` | The same cross-subject check as every other endpoint |

---

## 9. Done

| Requirement | Line |
|---|---|
| Ranked, subject-scoped, for intent and surface | `abc.md:309` |
| Hybrid candidate generation | `abc.md:123` |
| Rerank on the seven signals | `abc.md:124` |
| Diversity limits | `abc.md:125` |
| Bounded candidate generation | `abc.md:191` |
| Policy engine removes prohibited, expired, contradicted | `abc.md:192` |
| Subject isolation at the query boundary | `abc.md:119` |
| Consent enforced before retrieval | `abc.md:53` |
| Response links to a trace | `abc.md:322` |

---

## 10. Pending

| Missing | Requirement |
|---|---|
| **Token budget** is accepted but not applied — a context budget needs to know what the pack looks like, which is endpoint 5 | `abc.md:309`, `:125` |
| **Locale** is accepted but not used in ranking | `abc.md:309` |
| **Negative feedback** reads the audit trail; `POST /v1/feedback` is still a stub, so nothing writes it yet | `abc.md:124` |
| **Correction status** is not a separate signal — superseded memories are excluded entirely rather than scored down | `abc.md:192` |
| **Recent explicit signals from operational storage** are not a third candidate source | `abc.md:191` |
| **No golden set** measuring top-k precision | `abc.md:148`, `:353` |
| **P95 latency is unmeasured** against the 250 ms budget | `abc.md:170` |

One deviation, stated:

| Ours | Note |
|---|---|
| Ranking weights | `abc.md:124` names the signals but gives no weights. Ours are in `memory/retrieval.py`. |
