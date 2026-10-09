# Overview — screen document

**Screen 1 of 7** · **Route:** `/` · **File:** `apps/memory-console/app/page.tsx`

**Requirement — `abc.md:339`:** *"Overview: Service health, ingestion lag,
retrieval SLO, fallback rate, quality metrics, experiment status, and deletion
backlog."*

All seven are on the screen.

---

## Call flow

Runs on load, then every ten seconds.

1. **`refresh()`** — `app/page.tsx`
   Asks for health first. If the service is down there is nothing else worth
   asking for, so it stops there.

2. **`getHealth()`** → **`get("/health")`** — `lib/api.ts`
   → `/api/backend/health` on the console's own server, which checks the login
   pass and forwards it.
   → **Backend `GET /health`** — answers `{"status": "ok"}`, touches no store.

3. **`get<Metrics>("/metrics")`** — `lib/api.ts`
   → **Backend `GET /metrics`** → **`db.ingestion_metrics()`** in `memory/db.py`,
   which reads **PostgreSQL** six times:

   | Number | Table | Function |
   |---|---|---|
   | Event counts, rejection rate and reasons | `audit_log` | inline in `ingestion_metrics()` |
   | Ingestion lag | `ingested_event` | inline |
   | Retrieval SLO | `request_latency` | `retrieval_slo()` |
   | Fallback rate | `fallback_event` | `fallback_rate()` |
   | Deletion backlog | `deletion_job` | `deletion_backlog()` |
   | Experiment status | `experiment_cohort` | `experiment_status()` |
   | Quality metrics | `golden_run` | `latest_golden_run()` |

4. **`setMetrics()`** — renders seven cards, one per required view.

---

## Where the numbers come from

Three of the seven already had a source. The other four needed one, and
migration `006_observability_and_experiments.sql` added it.

**Retrieval SLO.** `abc.md:170` sets a 250 ms P95 budget. A percentile cannot be
derived from a running average, so a middleware in `memory/api.py` writes one row
per request into `request_latency` and `retrieval_slo()` computes P50, P95 and
P99 from them. Only `POST /v1/memories/search` and `POST /v1/context/compose` are
measured — the two routes the budget names. Mixing the write path in would
flatter the number, because accepting an event is far faster than searching a
graph. The write is best-effort: a metrics failure must never fail the request it
was measuring.

**Fallback rate.** Every context composition writes a row into `fallback_event`
saying whether it answered without memory and why. Both halves are recorded, so
the rate has a denominator as well as a numerator. The reasons are shown beside
the rate because a high rate caused by paused consent is a different problem from
one caused by an unhealthy graph — and `abc.md:167` makes falling open correct
behaviour, not an error.

**Deletion backlog.** Counted from `deletion_job`, with the age of the oldest
unfinished job. Age matters as much as count: `abc.md:361` blocks release
outright if deletion propagation is incomplete, so one job stuck for an hour is a
release blocker while ten jobs a second old are not.

**Experiment status.** `abc.md:146` asks for cohort allocation that is
*consistent*, meaning a subject keeps its cohort. So `experiment_cohort` stores
the allocation rather than deciding it per request.

**Quality metrics.** The most recent row of `golden_run`, written by
`scripts/run_golden_set.py`. Empty until the golden set has been run once, and
the card says which command to run.

---

## What the screen judges

Only two things, and both come straight from the requirement.

- **Within budget** — `retrieval_slo.within_budget` is true when P95 is at or
  under 250 ms, and null when nothing has been measured yet. A null shows as
  *no samples yet* rather than a green tick.
- **Deletion backlog clear** — anything unfinished prints the `abc.md:361` line
  about release being blocked.

Nothing else is interpreted. Where a number has no samples the screen prints a
dash, because an operations console that guesses is worse than one that admits it
has not measured anything.

---

## Functions

| Function | File | Why it exists |
|---|---|---|
| `OverviewPage()` | `app/page.tsx` | The screen; holds health, metrics and any failure. |
| `refresh()` | same | One poll: health, then metrics. Stops early if the service is down. |
| `asDuration()` | same | Seconds into words — `42s`, `18m`, `2.1h`. Seconds alone are unreadable past a minute. |
| `asPercent()` | same | A rate as a percentage, or a dash when nothing has happened yet. |
| `getHealth()`, `get()` | `lib/api.ts` | The two calls, through the gateway. |
| `record_request_latency` | `memory/api.py` | The middleware that times every request. Best-effort, so measuring cannot break the measured. |
| `retrieval_slo()`, `fallback_rate()`, `deletion_backlog()`, `experiment_status()`, `latest_golden_run()` | `memory/db.py` | One function per required number. |
| `record_fallback()` | `memory/db.py` | Called once per context composition, fallback or not. |

---

## Worth pointing at

**Stop the backend and watch this screen.** Within ten seconds it turns red and
prints the two commands that start the API and the worker.

**Log in as `user_003` (locally, with `DEMO_PASSWORD`) and open Context preview.** That
subject is in the memory-disabled arm, so it gets an explicit no-memory package —
and the fallback rate here moves, with `memory_disabled_cohort` named as the
reason. The baseline behaves exactly like a real fallback, which is what makes it
a fair comparison.
