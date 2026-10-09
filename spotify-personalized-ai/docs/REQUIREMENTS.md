# Original Requirements — checkable summary

Distilled from the 27-screenshot dossier in `~/Downloads/project-context/`
(same content as `abc.md`). This is the thing to check work against.
Section numbers below match the dossier.

---

## §9 Evaluation — 100 marks

| Category | Marks |
|---|---|
| AI workflow implementation | **15** |
| Graph, embeddings, and memory quality | **15** |
| Reliability, privacy, and safety | **14** |
| Backend and API implementation | **12** |
| Product experience | 10 |
| Product understanding and business framing | 8 |
| Deployment readiness | 8 |
| Documentation quality | 6 |
| Demo quality | 6 |
| Team collaboration | 6 |

> **Scoring rule.** No bonus marks. **Any critical privacy, security,
> cross-subject isolation, or deletion failure blocks release regardless of
> aggregate score.** Those four are pass/fail, not scored.

---

## §7.3 The ten endpoints — fixed list, nothing extra

| # | Endpoint | Purpose |
|---|---|---|
| 1 | `POST /v1/events` | Accept an eligible event; validate subject, consent, **schema**, idempotency, source |
| 2 | `POST /v1/memories/extract` | Event to typed candidate memories for deterministic validation |
| 3 | `POST /v1/memories` | Create memory; return stable ID, graph version, policy state |
| 4 | `POST /v1/memories/search` | Ranked subject-scoped memories for intent, surface, locale, token budget |
| 5 | `POST /v1/context/compose` | Apply policy, build the context package |
| 6 | `PATCH /v1/memories/{memory_id}` | Correct, supersede, expire — under optimistic concurrency |
| 7 | `DELETE /v1/memories/{memory_id}` | Start cross-store deletion, return job id |
| 8 | `GET /v1/deletions/{job_id}` | Graph, vector, cache, operational-store, backup-policy status |
| 9 | `POST /v1/feedback` | Relevance / correction / rejection, without self-validating model output |
| 10 | `GET /v1/traces/{trace_id}` | Authorized retrieval and policy decisions, sensitive fields redacted |

**Request flow:** gateway authenticates workload **and subject scope**, validates
the typed payload, attaches a correlation id, and returns a result with policy
and trace metadata. Writes are idempotent. Async work returns a job state.
**Errors use stable codes** for: validation, authorization, policy denial,
conflict, dependency timeout, retryable failure.
Logs carry identifiers and outcomes, **not raw private content**.

---

## §6.3 Event contract — the required fields

> Versioned interaction-event schema with **subject scope, surface, locale,
> timestamp, event type, consent state, source identifier, and idempotency key.**

Note §6.3 includes **event type**; §5.4 lists the other seven.

**§5.4 Interaction capture also requires:**
- Accept AI interactions, playback actions, saves, follows, skips, explicit
  preference statements, and corrections — through **versioned** event contracts.
- Separate raw-event retention from memory retention; not every event becomes a
  retrievable memory.
- Reject **malformed, unauthenticated, out-of-policy, or unsupported-version**
  events **before graph processing**.

---

## The five memory types (§7.2 step 1)

`explicit_preference`, `exclusion`, `correction`, `episode`,
`candidate_preference` (approved inferred).

For each, document: definition, example, **counterexample**, sensitivity,
retention, retrieval eligibility.

**The key distinction (Data Science Lead):** "Played focus music this morning"
is an **episode**. "Prefers low-vocal focus playlists while working" becomes a
**durable preference only after explicit confirmation or repeated evidence.**

---

## §5.4 Requirements by area

**Extraction and entity resolution**
- Classify into the five types plus non-memory interactions.
- Resolve artists, tracks, albums, playlists, shows, episodes, topics,
  activities, contextual concepts to canonical ids.
- Deduplicate semantically equivalent statements, keep source lineage.
- Confidence and policy class from **deterministic rules plus structured model
  output** — not the model alone.

**Temporal graph**
- Facts as versioned nodes and relationships with `valid_from`, `valid_to`,
  `recorded_at`, `source`, `confidence`, `status`.
- Contradiction, supersession, expiry, correction **without erasing audit
  history prematurely**. Corrections supersede; they never silently overwrite.
- **Subject isolation at the query boundary** — no traversal across identity
  scopes.
- Graph constraints and idempotent upserts.

**Embeddings and retrieval**
- Embed only approved fields; store vectors under the **same stable memory id**
  as the graph, so erasure is complete.
- Hybrid candidates: graph traversal (relational) plus vector similarity
  (semantic).
- Rerank by **seven signals**: intent fit, explicitness, confidence, recency,
  repetition, surface policy, negative feedback.
- Diversity and context-budget caps so one preference cannot dominate.

**MCP tools — exactly five**
`search_memory`, `add_explicit_preference`, `correct_memory`, `delete_memory`,
`explain_memory_use`.
Typed I/O schemas, authenticated subject binding, authorization checks, rate
limits, audit events. **Never expose a generic graph query tool to the model.**

**Context composition**
- Structured package: memory id, fact, type, confidence, time, source class,
  relevance reason.
- Exclude expired, contradicted, disallowed, low-confidence, off-policy.
- **Stored free text is untrusted data, isolated from system instructions**
  (prompt-injection defense).
- Deterministic **no-memory fallback**; record which memories influenced the
  response.

**User control, privacy, safety**
- Review, correction, deletion, **pause**, **opt-out** with propagation status.
- Do not convert transient emotional cues into durable memory by default.
- Retention by type, geography, age-related policy, consent state.
- Deletion across graph, vector, cache, operational metadata, backups.

**Observability**
- Trace intake, transformation, graph write, embedding write, retrieval,
  reranking, policy filtering, context injection.
- Monitor ingestion lag, write failures, retrieval latency, cache
  effectiveness, fallback rate, deletion backlog, policy rejection rate.
- Dead-letter handling and **idempotent replay**.
- Redact sensitive payloads from logs, keep investigation identifiers.

---

## §5.5 Non-functional

| Area | Requirement |
|---|---|
| Reliability | Retrieval **fails open** to a non-personalized response. No partial or cross-subject context after timeout or auth failure. |
| Performance | **P95 retrieval plus composition within 250 ms** for the pilot. |
| Security | **Every read and write binds to an authenticated subject and service identity.** |
| Privacy | Purpose-limited, minimal, reversible. |
| Maintainability | Schemas, scoring policies, MCP tools, prompts **versioned** and independently testable. |
| Scalability | Async writes, idempotency, queue backpressure. |
| Observability | Every response links to a trace. |
| Deployment | Health checks, env config, migrations, rollback, runbooks. |

---

## §7.5 AI safety rules

- Structured prompts at **narrow decision points**, not one unconstrained agent.
- System instruction states role, allowed taxonomy, prohibited inferences,
  subject boundary, temporal rules, and **return "no memory" when evidence is
  insufficient**.
- Validation rejects unknown entities, unsupported claims, **invented memory
  ids**, invalid timestamps, policy-ineligible types, and context items without
  provenance.
- **Memory safety:** never write a durable fact solely because the model
  generated it. Only eligible user or product evidence creates memory.

---

## §7.7 Test areas

Functional, Contract (backward compatibility), AI output, **Security
(cross-subject isolation, authorization bypass, prompt injection via stored
content, tool abuse, replay, rate limits)**, Privacy, Performance,
Resilience, Experience.

---

## §10 Final deliverables

- GitHub repo: code, infrastructure, tests, schemas, prompts, policy rules, docs
- **Deployed application link in the README**
- End-to-end demo video on Google Drive, "anyone with the link can view"
- README: setup, architecture, screenshots, APIs, MCP tools, env vars, data
  model, limitations, team contribution
- Synthetic data, graph schema, vector metadata schema, event contracts, MCP
  tool definitions, golden evaluation set, workflow files, runbooks
- Release evidence: quality results, security and privacy checks, latency,
  resilience, deletion propagation, known risks, rollback readiness
