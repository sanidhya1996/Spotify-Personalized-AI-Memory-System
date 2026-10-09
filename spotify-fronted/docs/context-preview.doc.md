# Context preview — screen document

**Route:** `/context`
**File:** `apps/memory-console/app/context/page.tsx`
**Requirement:** `abc.md:341` — *"Context preview: Enter a current intent and
surface; display candidate retrieval, ranking, policy removals, final context
pack, and token usage."*

---

## What this screen is for

It answers the one question the whole system exists to make answerable: *why
did the assistant say that?*

So it does not show a result. It shows the pipeline, in the order the backend
runs it — what was found, how it was scored, what policy took away, what
survived, and what it cost in tokens. An operator can look at a bad answer and
point at the step that caused it.

---

## Call flow

This screen starts when the operator presses **Compose context**.

1. **`run()`** — `app/context/page.tsx`
   Clears the previous result, then makes the two calls below in order.

2. **`post("/v1/memories/search", …)`** — `lib/api.ts`
   POSTs the JSON to `/api/backend/...` on the console's own server, which checks
   the login pass, writes the logged-in user's id in, forwards it, and turns a failure into an `ApiFailure` carrying
   the backend's stable code.
   → **Backend endpoint 4, `POST /v1/memories/search`**, which reads
   **Neo4j** (the memory graph and its 384-dimension vectors) and
   **PostgreSQL** (negative feedback, one of the six scoring signals).
   Comes back with the candidates, each score broken into its signals, the
   count considered, and a trace id.

3. **`setSearch(found)`** — `app/context/page.tsx`
   Renders section **1 · Candidate retrieval and ranking**: one row per
   candidate, the total score, and a `ScoreBar` per signal with the backend's
   weight beside its name.

4. **`post("/v1/context/compose", …)`** — `lib/api.ts`
   The same client, through the same gateway route.
   → **Backend endpoint 5, `POST /v1/context/compose`**, which checks consent
   in **PostgreSQL**, runs retrieval against **Neo4j** again, applies the
   policy registry, trims to the token budget and renders the fenced block.
   Comes back with the pack, everything removed and why, the token estimate,
   the per-request fence markers and a trace id.

5. **`setPack(composed)`** — `app/context/page.tsx`
   Renders sections **2 · Policy removals** and **3 · Final context pack and
   token usage**, and marks every candidate in section 1 as *in pack* or
   *not in pack* by comparing memory ids.

6. **On failure** — `ErrorNote` in `components/ui.tsx`
   Shows the stable code, the message and the correlation id, because that id
   is what finds the request in the backend's audit log.

Nothing is cached and nothing is stored in the browser - not even a token,
which the browser never sees. Every press is a fresh pair of calls.

---

## Why two calls and not one

`POST /v1/context/compose` alone would answer the question with no working
shown. It returns the final pack and a list of removals, but not the candidates
that lost, and not the score breakdown that decided the order.

`POST /v1/memories/search` returns exactly that. Calling both is what turns the
screen from a result into an explanation.

---

## Functions this screen uses

### `app/context/page.tsx`

| Function | Why it exists |
|---|---|
| `ContextPreviewPage()` | The screen: holds the four request fields and the two results. |
| `run()` | Makes the search and compose calls in order and keeps whichever arrive. |
| `WEIGHTS` (constant) | The backend's six scoring weights, shown beside each signal so a value can be read as a contribution. |
| `kept` (derived) | The set of memory ids in the final pack, so a candidate row can say whether it survived. |

### `lib/api.ts`

| Function | Why it exists |
|---|---|
| `post()` | Sends JSON to the gateway route and normalises errors. No token, no headers to remember. |
| `useSubject()` (`lib/useSubject.ts`) | The logged-in user's id, shown in the request card. |
| `capture()` | `app/context/page.tsx` | Every message sent in "Talk to Spotify's AI" is also captured as `POST /v1/events`, the way a real surface records interactions; the worker decides what is worth remembering. |
| `findSongs()` | same | The songs demo: searches with the request plus the top preference, skipping excluded genres (`app/api/songs/route.ts`). |
| `ApiFailure` | An error that carries the backend's stable code and correlation id, not just a message. |
| `toFailure()` | Unwraps the backend's `{"detail": {code, message, correlation_id}}` envelope. |

### `lib/types.ts`

| Type | Why it exists |
|---|---|
| `SearchResult`, `RankedMemory` | The search response, field for field as the backend returns it. |
| `ContextPackage`, `ContextItem` | The compose response, including the fence markers and the token estimate. |
| `ApiError` | The error envelope, so a screen branches on `code` and never on English. |

### `components/ui.tsx`

| Function | Why it exists |
|---|---|
| `Card` | A titled panel; one per pipeline step. |
| `Field` | A label above a control, for the four request fields. |
| `Button` | The compose action. |
| `Badge` | A pill for memory type, source class and in-pack state. |
| `typeTone()` | Picks a colour per memory type — green for stated, amber for inferred, red for an exclusion — so the difference is visible without reading. |
| `ScoreBar` | A 0-to-1 bar, used for every signal. |
| `Stat` | One number with a caption, for the counters. |
| `ErrorNote` | The failure, with its stable code and correlation id. |

---

## What the screen shows, against the requirement

| `abc.md:341` asks for | Where it is on the screen |
|---|---|
| Enter a current intent and surface | Section **The request** — subject, surface, intent, token budget |
| Candidate retrieval | Section 1 — *candidates considered*, and one row per candidate |
| Ranking | Section 1 — the total score, plus a bar per signal with its weight |
| Policy removals | Section 2 — every removal with the reason the backend gave |
| Final context pack | Section 3 — the items, and the fenced text the model actually receives |
| Token usage | Section 3 — estimate against budget, as a number and a bar, with the remainder |

---

## Two things worth pointing at in a demo

**The no-memory answer.** Run it for `user_005`, whose consent is paused. The
pack comes back with `no_memory: true` and a reason, and the screen says so
plainly. `abc.md:135` requires an explicit no-memory answer rather than a
silent empty pack, and `abc.md:158` requires the experience to carry on without
memory.

**The fence.** The block in section 3 opens with a warning line and wraps the
memory text in markers carrying a random suffix, printed underneath. The
suffix changes on every request, so stored text cannot close the fence and
smuggle an instruction into the prompt.

---

## What is not on this screen

- **The reranker.** `abc.md` allows an LLM reranker to be *tested* on a bounded
  candidate set. The backend ranks deterministically, so there is no second
  ordering to compare against.
- **A side-by-side memory-on / memory-off comparison.** That belongs to the
  quality review screen, which needs golden sets the backend does not have.
