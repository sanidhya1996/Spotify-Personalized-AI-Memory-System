# Code Flow Docs

One file per API. Each traces the request from the moment it arrives:
which function runs, what it calls next, which file that lives in, where
the data goes, and what comes back — with one line per function.

| File | API | In one line |
|---|---|---|
| `api-1-events.flow.md` | `POST /v1/events` | Writes down that something happened |
| `api-2-extract.flow.md` | `POST /v1/memories/extract` | Decides what is worth remembering |
| `api-3-memories.flow.md` | `POST /v1/memories` | Saves it so it survives |
| `api-4-search.flow.md` | `POST /v1/memories/search` | Finds the ones that matter now |
| `api-5-compose.flow.md` | `POST /v1/context/compose` | Hands them to the AI, safely |
| `api-6-patch.flow.md` | `PATCH /v1/memories/{id}` | Correct or retire a memory |
| `api-7-delete.flow.md` | `DELETE /v1/memories/{id}` | Delete it everywhere |
| `api-8-deletions.flow.md` | `GET /v1/deletions/{job_id}` | Did every store actually clear? |
| `api-9-feedback.flow.md` | `POST /v1/feedback` | Say a memory was wrong or unhelpful |
| `api-10-traces.flow.md` | `GET /v1/traces/{id}` | See why it did what it did |
| `worker-memory-processor.flow.md` | *(not an endpoint)* | The worker that runs 2 and 3 on its own |

---

## How to read one

Each file has four parts:

1. **The path** — a tree of what runs, in order, top to bottom
2. **Every function, one line each** — a table, so you can scan it
3. **Where the data goes** — which database each step touches
4. **What can go wrong, and where** — every error, and the step it comes from

---

## How to read the arrows

```
├─ db.get_consent()                  memory/db.py
│     reads OUR consent record                      → 403
│     └──────────────────────────► POSTGRES
```

| Symbol | Means |
|---|---|
| `├─` `└─` | A step inside the one above it |
| `memory/db.py` | The file that function lives in |
| `──► POSTGRES` | This step talks to that store |
| `→ 403` | This step can end the request with that error |
| `[background]` | Runs **after** the reply is sent; the caller does not wait |

---

## The whole system, in five lines

```
1. events    a listener does something      → written down
2. extract   is it worth remembering?       → decided
3. memories  keep it                        → stored in Neo4j
4. search    what matters for this question → found and ranked
5. compose   hand it to the AI              → packaged safely

6. patch     "that's wrong" / "stop using that"
7. delete    remove it from every store
8. deletions did every store actually clear?
9. feedback  that was wrong / that helped
10. traces   why did it say that?
```

APIs 1 and 5 are called by Spotify's chat and player.
APIs 2 and 3 are run by the **memory processor**, not by anyone directly -
see `worker-memory-processor.flow.md`.
API 4 is used inside API 5, and by the console screens.

---

## If you want plain English instead

`../apis-explained.doc.md` describes all five with no code at all.

Each API also has a full document of its own — `../events.doc.md`,
`../memories-extract.doc.md`, and so on — covering the requirements it
meets and what is still missing.
