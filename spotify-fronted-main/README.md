# Spotify Personalized AI — frontend

The web app for the governed memory system: log in, tell Spotify's AI what
you like, and see exactly what it remembers, how it ranks it, and what reaches
the model - for your own account only.

**Backend:** https://github.com/omsemwal/spotify-personalized-ai
**Live app:** https://spotifyfrontend11.netlify.app

Stack is `abc.md:200`: *Next.js, React, Tailwind CSS*.

---

## Logging in

| | |
|---|---|
| New user | Open the app, choose **Create an account**: a user id nobody has (lower-case letters, digits, `_`) and a password of 8+ characters |
| Test users (local only) | `user_001` ... `user_005`, with the password set as `DEMO_PASSWORD` in the backend's private `.env` |

After logging in, **every page shows only your own data**. How that is
enforced: [how-authentication-works.doc.md](docs/how-authentication-works.doc.md).

---

## What is in here

`abc.md:252-253` names two apps. Both are here.

```
apps/
  memory-console/     the main app - login and the seven screens (use this one)
  memory-controls/    the listener review page abc.md:253 names, same login
```

### The seven screens (`apps/memory-console`)

Exactly the seven of `abc.md:339-345`, in the document's own order.

| # | Screen | What it shows |
|---|---|---|
| 1 | **Overview** | Service health, ingestion lag, retrieval SLO against the 250 ms budget, fallback rate, write failures, cache hit rate, policy rejections, quality, experiment status, deletion backlog |
| 2 | **Memory explorer** | Your memories: timeline, relationships, source, confidence, status |
| 3 | **Context preview** | **Talk to Spotify's AI** in one box: you get an answer from your memories (ranking, policy removals, the final pack, token usage, songs), and it learns from what you say |
| 4 | **Correction and deletion** | Correct, expire, remove - with per-store propagation and no silent partial completion |
| 5 | **Schema and policy** | Allowed fields, contract version, retention by type, geography and age, each memory type's definition and example - read-only |
| 6 | **Quality review** | Golden-set runs, failure clusters, multilingual and contradiction cases, memory-enabled comparison |
| 7 | **Audit trace** | Decisions behind one response, memory ids, timestamps, redacted outcomes |

The top bar shows who is logged in, turns memory **on / paused / off**
(`abc.md:136` - pause and opt-out), and logs out.

### The listener page (`apps/memory-controls`)

`abc.md:51` asks for five listener paths - review, correct, remove, pause, opt
out. All five work here, in plain language, and also in the main app.

---

## Screenshots

From the live site. All eight are in the backend repository's README.

| Context preview | Memory explorer |
|---|---|
| ![Context preview](https://raw.githubusercontent.com/omsemwal/spotify-personalized-ai/main/docs/screenshots/02-context-preview.png) | ![Memory explorer](https://raw.githubusercontent.com/omsemwal/spotify-personalized-ai/main/docs/screenshots/03-memory-explorer.png) |

---

## Running it locally

Start the backend first (in its repository: `./start-backend.cmd`, or see its
`RUNNING.md`). Then, here:

```bash
./start-frontend.cmd        # Windows: both apps, http://localhost:3000 and :3001
```

or one app at a time:

```bash
cd apps/memory-console && npm install && npm run dev     # http://localhost:3000
cd apps/memory-controls && npm install && npm run dev    # http://localhost:3001
```

**One-time setup per app** - both need the backend's address and its signing
secret, which they use only to check the pass a person gets at login:

```bash
cp apps/memory-console/.env.local.example  apps/memory-console/.env.local
cp apps/memory-controls/.env.local.example apps/memory-controls/.env.local
# then put the backend's MEMORY_JWT_SECRET in both
```

Neither variable is prefixed `NEXT_PUBLIC_`, so neither reaches a browser.

**To fill in Quality review**, run the golden set once in the backend
repository: `python scripts/run_golden_set.py`.

---

## Deploying

`netlify.toml` builds `apps/memory-console` on Netlify (Vercel also works).
Set two environment variables in the site settings:

| Variable | Value |
|---|---|
| `MEMORY_API_BASE_URL` | the deployed backend, e.g. `https://memory-api.onrender.com` |
| `MEMORY_JWT_SECRET` | the same secret the backend uses |

---

## Documents

One per screen: what it shows, which requirement line it comes from, the call
flow through to the store, and the functions behind it.

| | |
|---|---|
| [overview.doc.md](docs/overview.doc.md) | Screen 1 |
| [memory-explorer.doc.md](docs/memory-explorer.doc.md) | Screen 2 |
| [context-preview.doc.md](docs/context-preview.doc.md) | Screen 3 |
| [correction-and-deletion.doc.md](docs/correction-and-deletion.doc.md) | Screen 4 |
| [schema-and-policy.doc.md](docs/schema-and-policy.doc.md) | Screen 5 |
| [quality-review.doc.md](docs/quality-review.doc.md) | Screen 6 |
| [audit-trace.doc.md](docs/audit-trace.doc.md) | Screen 7 |
| [memory-controls.doc.md](docs/memory-controls.doc.md) | The listener page |
| [how-authentication-works.doc.md](docs/how-authentication-works.doc.md) | Login, and why you only ever see your own data |

---

## How the code is laid out

Both apps share the same shape.

| | |
|---|---|
| `app/login/page.tsx` | Log in / sign up |
| `app/api/auth/[action]/route.ts` | signup, login, logout, me - keeps the pass in an httpOnly cookie |
| `app/api/backend/[...path]/route.ts` | The gateway: checks the pass, writes your user id in, forwards |
| `app/<screen>/page.tsx` | One screen |
| `app/api/songs/route.ts` | The songs demo (iTunes previews) - console only |
| `components/Nav.tsx` | The sidebar - one entry per required screen (console only) |
| `components/SubjectBar.tsx` | Logged in as, memory on / paused / off, log out (console only) |
| `components/ui.tsx` | Card, Field, Button, Badge, ScoreBar, Stat, ErrorNote |
| `lib/session.ts` | Reads and checks the login pass |
| `lib/api.ts` | The only place pages call the backend from |
| `lib/types.ts` | Response shapes, mirroring the backend's Pydantic models |
