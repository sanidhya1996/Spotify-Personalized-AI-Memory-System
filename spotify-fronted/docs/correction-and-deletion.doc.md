# Correction and deletion — screen document

**Screen 4 of 7** · **Route:** `/corrections` · **File:** `apps/memory-console/app/corrections/page.tsx`

**Requirement — `abc.md:342`:** *"Correction and deletion: Correct or remove
eligible memories, show propagation status, and prevent silent partial
completion."*

---

## Call flow

Three steps on one page, in order.

**1 · Find the memory**

1. **`find()`** → **`post("/v1/memories/search", …)`** — searches on the **chat**
   surface, because the policy registry allows the most types there, so nothing
   eligible is hidden from an operator.
   → **Neo4j** (graph + vectors), **PostgreSQL** (negative feedback).

2. **`choose(memory)`** — copies the fact into the editable field and clears
   anything left over from the previous memory.

**2 · Correct or expire**

3. **`correct()`** → **`patch("/v1/memories/{id}", …)`**
   → **Backend `PATCH /v1/memories/{memory_id}`** → **`graph.supersede()`** in
   `memory/graph.py`: creates the new memory, sets the old one's `valid_to`,
   flips its status to `superseded`, and links them with `:SUPERSEDES`.
   A background task writes the new embedding to the **Neo4j vector index**, and
   another writes the audit row to **PostgreSQL**.
   The screen then repoints itself at the new memory the correction created.

4. **`expire()`** → the same endpoint with `operation: "expire"`
   → **`graph.expire_one()`** closes the memory without replacing it.

**3 · Remove it everywhere**

5. **`remove()`** → **`del("/v1/memories/{id}?subject_id=…")`**
   → **Backend `DELETE /v1/memories/{memory_id}`** → revokes retrieval
   eligibility in **Neo4j** immediately (`abc.md:97`), records the job in
   **PostgreSQL**, and returns a job id. The stores are cleared behind it.

6. **`watch(jobId)`** → **`get("/v1/deletions/{job_id}?subject_id=…")`** once a
   second, up to fifteen times, until the job stops being pending.
   → **PostgreSQL** — one status per store.

---

## "Prevent silent partial completion"

This is the demanding clause, and it shapes the whole third step.

Deletion has to reach the **graph**, the **vector index**, the **cache** and the
**operational store**, and a **backup** inside its retention window cannot be
cleared at all. So the screen:

- never stops polling while the job is pending or in progress;
- shows **one line per store**, never a single summary word;
- computes `unfinished` — any store not in `deleted`, `nothing_to_delete` or
  `retained_by_policy` — and when that list is non-empty prints **"This deletion
  is NOT complete"**, names the stores, and says *do not report this to the
  listener as deleted*;
- when every store is accounted for, explains that `retained_by_policy` on the
  backup is the honest answer for a backup inside its window, not a failure.

Verified live: `graph=deleted · vector=deleted · cache=deleted ·
operational=deleted · backup=retained_by_policy`.

---

## Why the version field is on the screen

`abc.md:313` requires optimistic concurrency. The correction sends the version
last seen; if the memory moved on in between, the backend refuses with
`409 CONFLICT` and names the version it is actually at, so nobody's edit is
silently lost. The field is editable precisely so that refusal can be
demonstrated: set it to 99 and press **Correct it**.

Verified live: `409 CONFLICT`.

---

## Functions

| Function | File | Why it exists |
|---|---|---|
| `CorrectionAndDeletionPage()` | `app/corrections/page.tsx` | The screen; holds the candidate list, the chosen memory, the draft wording, the version and the deletion job. |
| `find()` | same | Lists this subject's memories to act on, on the widest surface. |
| `choose()` | same | Selects one and clears the previous result. |
| `correct()` | same | Supersedes the memory and repoints the screen at the correction. |
| `expire()` | same | Closes the memory without replacing it. |
| `remove()` | same | Starts deletion, then watches it. |
| `watch()` | same | Polls the job until it is no longer in progress — this is what stops a partial deletion looking finished. |
| `unfinished` | same | Any store not accounted for. Drives the red warning. |
| `STORE_MEANING` | same | What each store state means in words, so `nothing_to_delete` is not read as a failure. |
| `patch()`, `del()`, `get()` | `lib/api.ts` | The three verbs this screen needs, through the gateway. |

---

## Not on this screen

- **Subject-wide deletion.** The backend deletes one memory per job; there is no
  "delete everything for this subject" endpoint.
- **Retry for a failed store.** The screen reports a store that did not clear, but
  the backend has no endpoint to retry that one store.
