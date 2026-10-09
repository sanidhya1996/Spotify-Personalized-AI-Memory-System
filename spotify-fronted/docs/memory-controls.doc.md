# Memory controls — app document

**The second app** · **Route:** `/` on port 3001 · **File:** `apps/memory-controls/app/page.tsx`

**Requirement — `abc.md:253`:** *"memory-controls/ # Review, correction, deletion UI"*
**Requirement — `abc.md:51`:** *"Memory Control Experience: User-facing controls to
review, correct, remove, pause, or opt out of eligible memory behavior."*
**Requirement — `abc.md:136`:** *"Provide review, correction, deletion, pause, and
opt-out paths with clear state and propagation status."*

---

## Who this is for, and why it is a separate app

The console is for operators. This is for the listener the memories are about.

That difference is not cosmetic. From the leadership transcript, the Product Design
Lead: *"Users should not see a technical graph. They need a clear experience:
'Spotify remembered this preference,' with the ability to correct or remove it."*
And `abc.md:175`: *"Use plain-language provenance and avoid exposing raw graph
internals."*

So this app shows no memory ids, no confidence scores, no relevance scores, no
entity identifiers and no policy vocabulary. A `candidate_preference` is shown as
**"We noticed this"**, with the words *a guess, not something you said* beside it.

---

## You only ever reach your own account, and that is the point

The listener logs in with their own user id and password (`app/login/page.tsx`,
standing in for the Spotify session a real deployment would read). There is no
way to switch to anybody else.

`app/api/backend/[...path]/route.ts` checks the login pass (`lib/session.ts`),
takes the user id from it, and **injects it into every request**: into the JSON
body on `POST` and `PATCH`, and into the query string on `GET` and `DELETE`,
overwriting anything the browser sent. No pass means *"Please log in"*.

The page never mentions a subject anywhere. So the strongest requirement in the
document — one listener can never reach another listener's memories — holds here
by construction rather than by a check.

Verified: logged in as `user_001`, a request whose body claims
`subject_id: "user_002"` is served as `user_001` regardless, because the server
overwrites the field.

The gateway is also **not a general proxy**. `ALLOWED` is a closed list of path
patterns: search, one memory, one deletion job, feedback, consent and health. A listener's
app has no business reading `/metrics` or calling extraction.

Verified: `GET /metrics` through this app returns `403 NOT_ALLOWED_HERE`.

---

## Call flow

**Review** — `abc.md:136`, path 1

1. **`load()`** → **`post("/v1/memories/search", …)`** with a broad intent and the
   **chat** surface, which allows the most memory types, so nothing is hidden from
   the person it belongs to. No subject is sent; the gateway adds it.
   → **Neo4j** (graph and vectors), **PostgreSQL** (negative feedback).

**Correct** — path 2

2. **`saveCorrection()`** → **`patch("/v1/memories/{id}", …)`**
   → **`graph.supersede()`** — the old wording is closed and kept as history, never
   overwritten, so a correction can itself be reviewed later.
   Then **`post("/v1/feedback", …)`** with `kind: "correction"`, so the service
   records that this came from the listener rather than from us.

3. **`sayItsWrong()`** → **`post("/v1/feedback", …)`** with `kind: "rejection"`.
   Offered only on memories we inferred. `abc.md:149` — negative feedback from the
   listener always counts, even on a guess.
   → **PostgreSQL**.

**Remove** — path 3

4. **`remove()`** → **`del("/v1/memories/{id}")`**, then
   **`get("/v1/deletions/{job_id}")`** once a second until the job finishes.
   → **Neo4j**, the **vector index**, **Redis**, **PostgreSQL**.

---

## Pause and opt out

`abc.md:136` asks for five paths. All five work.

**Pause** — path 4

5. **`setConsentState("paused")`** → **`patch("/v1/consent", { state })`**
   → **Backend `PATCH /v1/consent`** → **`db.set_consent()`** → **PostgreSQL**
   `consent`, then **`cache.forget_subject()`** clears **Redis** so a paused
   listener cannot be answered from a cache warmed while consent was granted
   (`abc.md:141`). The consent change is audited inline rather than in the
   background, so it cannot be lost.

**Opt out** — path 5, the same call with `state: "denied"`.

`abc.md:53` enforces consent before memory reaches retrieval, so both take effect
on the **very next request**: `POST /v1/context/compose` returns an explicit
no-memory package with the reason, rather than an error. The experience carries on
without memory, which is `abc.md:158`.

### Why `PATCH /v1/consent` exists at all

Section 7.3's API table lists ten endpoints and none of them changes consent.
Section 5.4 (`abc.md:136`) requires the pause and opt-out paths. That is a gap in
the document, not a feature invented here: the consent table, the three states and
the enforcement all existed already — there was simply no way for the person the
consent belongs to to set it.

### Pausing is not deleting

`abc.md:137` keeps them separate, and the app says so in as many words. While
paused, nothing is used and nothing is removed, so turning memory back on restores
everything. To remove something for good the listener uses **Remove**, which
reports its own cross-store propagation.

Blurring the two would be the easiest way to mislead someone about what just
happened to their data.

---

## Propagation status, in plain words

`abc.md:136` asks for *"clear state and propagation status"*, and `abc.md:342`
forbids letting a partial removal look complete. The app computes `stillThere` —
any store not `deleted`, `nothing_to_delete` or `retained_by_policy` — and says one
of two things:

- **Not fully removed yet** — *"We could not finish removing this everywhere. It
  may still be used. Please try again in a moment."*
- **Removed** — *"Gone from everywhere we use it. A copy may remain in a backup
  until that backup expires, which we cannot delete early — but nothing will read
  it."*

The second sentence is the honest version of `retained_by_policy` for someone who
does not know what a backup retention window is.

---

## Functions

| Function | File | Why it exists |
|---|---|---|
| `MemoryControlsPage()` | `app/page.tsx` | The whole app; holds the memories, the draft wording and the removal progress. |
| `load()` | same | Everything we hold about this listener. |
| `saveCorrection()` | same | Corrects the wording and records that the listener asked for it. |
| `sayItsWrong()` | same | Rejects a guess without rewriting it. |
| `remove()` | same | Deletes, then watches every store until the job finishes. |
| `stillThere` | same | Any store unaccounted for. Decides which of the two messages is shown. |
| `IN_PLAIN_WORDS` | same | Each memory type in language for the person it is about. |
| `weGuessed()` | same | Whether we inferred it, which decides the softer framing and the extra way to say no. |
| `proxy()` | `app/api/backend/[...path]/route.ts` | Checks the login pass, refuses paths this app may not call, injects the logged-in user into body and query. |
| `readSession()` | `lib/session.ts` | Reads and checks the pass kept in the httpOnly cookie at login. |
| `LoginPage` | `app/login/page.tsx` | Log in / sign up. |
| `refuse()` | same | A refusal in the backend's own error envelope, so the app handles it like any other failure. |

---

## Running it

```bash
cd apps/memory-controls
cp .env.local.example .env.local     # add the backend MEMORY_JWT_SECRET
npm install && npm run dev           # http://localhost:3001
```

Log in as `user_005` (locally, with `DEMO_PASSWORD`) to see what a listener with paused
consent sees.
