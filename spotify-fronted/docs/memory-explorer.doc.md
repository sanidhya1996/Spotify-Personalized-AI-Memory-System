# Memory explorer — screen document

**Screen 2 of 7** · **Route:** `/memories` · **File:** `apps/memory-console/app/memories/page.tsx`

**Requirement — `abc.md:340`:** *"Subject-scoped memory explorer: Search only with
approved support or test identities; show timeline, graph relationships, source
type, confidence, and status."*

All five are on the screen.

---

## Call flow

1. **`load(query)`** — runs on arrival, and again whenever the subject or the
   surface changes. An empty search box becomes a broad intent, which is how the
   screen lists everything rather than only what matches a phrase.

2. **`post("/v1/memories/search", …)`** — `lib/api.ts`
   → `/api/backend/v1/memories/search` on the console's own server, which checks
   the login pass, writes the logged-in user's id in, and forwards it.
   → **Backend `POST /v1/memories/search`** → **`retrieval.search()`**:
   - **Neo4j** — `graph_candidates()` walks `:ABOUT` edges for relational matches
   - **Neo4j vector index** — `similar()` for semantic matches on `embedding_384`
   - **PostgreSQL** — `db.negative_feedback()`, one of the six scoring signals
   - the policy registry then drops types this surface may not use

3. **`setResult()`** — renders the counts, what policy hides on this surface, and
   the memories themselves.

Filtering by type happens in the browser on the results already returned; it is a
view control, not another query.

---

## Only your own memories

This is why there is no free-text subject box on this screen.

`abc.md:340` limits the explorer to approved identities. Here the only identity
is the one you logged in as: the gateway takes the user id from the login pass
and writes it into every request, so you see your own memories and nobody
else's. Verified: logged in as `user_001`, a request naming `user_002` is
served as `user_001`. See `how-authentication-works.doc.md`.

---

## The timeline

`RankedMemory` now carries the three temporal properties the graph has always
stored (`abc.md:117` — *"valid-from, valid-to, recorded-at, source-event
identifiers, confidence, and policy class"*), plus the status:

| Field | Shown as |
|---|---|
| `recorded_at` | **recorded** — when we wrote it down |
| `valid_from` | **valid from** — when it started being true |
| `valid_to` | **valid to**, or **still true** when null |
| `status` | a pill: `active`, `superseded` or `expired` |

The status colour carries the meaning: green while it is still true, amber once a
correction replaced it, grey once it expired. A superseded memory is not deleted
— `abc.md:118` requires corrections to supersede *"without erasing audit history
prematurely"* — so seeing one with a `valid_to` is the correction mechanism
working, not a fault.

Those fields were previously dropped between the graph and the response, which is
why this screen could list memories but not place them in time.

---

## What is shown, against the requirement

| Asked for | Shown |
|---|---|
| Timeline | `recorded_at`, `valid_from`, `valid_to` on every memory |
| Graph relationships | The canonical entities each memory is `:ABOUT` |
| Source type | The memory type, plus *stated* or *observed* |
| Confidence | On every memory, with the evidence count beside it |
| Status | `active`, `superseded` or `expired` |

Two things are shown beyond the list, because they explain what the operator is
looking at: how many candidates the graph held versus how many are retrievable on
this surface, and the per-type counts.

---

## Functions

| Function | File | Why it exists |
|---|---|---|
| `MemoryExplorerPage()` | `app/memories/page.tsx` | The screen; holds the query, surface, type filter and results. |
| `load()` | same | One search for the selected subject. Turns an empty box into a broad intent. |
| `sourceClass()` | same | Stated or observed — the distinction that matters most when judging whether a memory should have been used. |
| `when()` | same | One timestamp as a short local date and time, or a dash when absent. |
| `statusTone()` | same | Green while still true, amber once superseded, grey once expired. |
| `useSubject()` | `lib/useSubject.ts` | The subject the console is acting as, so the request body matches the token. |
| `as_datetime()` | `memory/retrieval.py` | Turns a Neo4j temporal value into a plain datetime, keeping a missing value as null rather than a misleading zero date. |

---

## Worth pointing at

**Switch the surface from chat to player.** Episodes and candidate preferences
disappear, and the **Held, but not retrievable on this surface** card names each
one and why. That is the policy registry being enforced, visible rather than
described.

**Correct a memory on the Correction screen, then come back here.** The
correction appears as a new `active` memory, and the original is still listed
with a `valid_to` and a `superseded` status — the history that `abc.md:118`
requires be kept.
