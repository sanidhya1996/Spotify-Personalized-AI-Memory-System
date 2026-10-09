"""Why this file exists
=====================

Three numbers the requirements ask us to monitor that nothing counted yet.

abc.md §5.4 Observability - "Monitor ingestion lag, write failures,
retrieval latency, cache effectiveness, fallback rate, deletion backlog, and
policy rejection rate."

Already counted elsewhere (memory/db.py): ingestion lag, retrieval latency,
fallback rate, deletion backlog. This file adds the other three:

    write_failures         events the worker could not turn into memory
    cache_effectiveness    how often the Redis idempotency cache saved a
                           duplicate write
    policy_rejection_rate  how many retrieved memories policy removed
                           before they reached the AI

Each is counted from records we already keep - the audit log and the trace
table - so there is no extra counter to keep in step.

Where it is used
----------------
memory/api.py, GET /metrics - added to what the console's Overview screen
reads. The Overview shows them in the "Write failures, cache and policy"
card.
"""

from memory import db


# Events the worker failed to process, out of all it tried.
def write_failures() -> dict:
    """The worker writes "processor.completed" or "processor.failed" to the
    audit log for every event (memory/processor.py)."""
    with db.connect() as conn:
        failed, completed = conn.execute(
            "SELECT count(*) FILTER (WHERE action = 'processor.failed'),"
            "       count(*) FILTER (WHERE action = 'processor.completed')"
            " FROM audit_log"
        ).fetchone()
        # Why recent events failed or stored nothing - fixed reasons only.
        reasons = conn.execute(
            "SELECT action, reason, count(*) FROM audit_log"
            " WHERE action IN ('processor.failed', 'processor.completed')"
            " AND reason IS NOT NULL"
            " GROUP BY action, reason ORDER BY count(*) DESC LIMIT 10"
        ).fetchall()
    tried = failed + completed
    return {"failed": failed, "processed": tried,
            "rate": round(failed / tried, 4) if tried else 0.0,
            "reasons": [{"kind": "failed" if action == "processor.failed" else "no memory",
                         "reason": reason, "count": count}
                        for action, reason, count in reasons]}


# How often a repeated event was answered from the cache instead of written again.
def cache_effectiveness() -> dict:
    """Redis holds each idempotency key for a day (memory/cache.py). A
    "duplicate" outcome means the cache recognised a repeat and nothing was
    written twice - that is the cache doing its job."""
    with db.connect() as conn:
        hits, misses = conn.execute(
            "SELECT count(*) FILTER (WHERE outcome = 'duplicate'),"
            "       count(*) FILTER (WHERE outcome = 'accepted')"
            " FROM audit_log WHERE action LIKE 'event.%%'"
        ).fetchone()
    lookups = hits + misses
    return {"hits": hits, "lookups": lookups,
            "hit_rate": round(hits / lookups, 4) if lookups else 0.0}


# Share of retrieved memories that policy removed before they reached the AI.
def policy_rejection_rate() -> dict:
    """Every composition records each memory as "included" or "excluded" in
    the trace table (memory/trace.py)."""
    with db.connect() as conn:
        excluded, included = conn.execute(
            "SELECT count(*) FILTER (WHERE decision = 'excluded'),"
            "       count(*) FILTER (WHERE decision = 'included')"
            " FROM trace_decision"
        ).fetchone()
    seen = excluded + included
    return {"excluded": excluded, "considered": seen,
            "rate": round(excluded / seen, 4) if seen else 0.0}


# All three together, for GET /metrics.
def summary() -> dict:
    return {
        "write_failures": write_failures(),
        "cache_effectiveness": cache_effectiveness(),
        "policy_rejection": policy_rejection_rate(),
    }
