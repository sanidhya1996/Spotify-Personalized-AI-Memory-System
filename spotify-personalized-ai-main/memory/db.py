"""Talking to PostgreSQL.

Three things this file does:

  1. get_consent(subject_id)  - what OUR record says about consent
  2. save_event(...)          - write one accepted event
  3. count_events(subject_id) - used by tests

abc.md:219 - PostgreSQL "stores consent state, ingestion status, tool
audit, experiments, feedback, and deletion jobs."

Plain SQL on purpose. No ORM, no models, no magic - what you read here is
what runs on the database.
"""

import atexit
from datetime import datetime, timedelta, timezone

from psycopg_pool import ConnectionPool

from memory import config

# abc.md:109 - "Separate raw event retention from memory retention."
# Raw events are short-lived; memories live much longer, on their own clock.
RAW_EVENT_RETENTION = timedelta(days=30)


_pool: ConnectionPool | None = None


def _get_pool() -> ConnectionPool:
    """One small pool, opened the first time it is needed.

    Opening a fresh TCP connection for every request is slow, so a few are
    kept open and handed out as needed.
    """
    global _pool
    if _pool is None:
        _pool = ConnectionPool(config.postgres_url(), min_size=1, max_size=5)
        # Close it tidily on shutdown, or Python complains about the
        # pool's background thread still running as it exits.
        atexit.register(close)
    return _pool


def close() -> None:
    """Shut the pool down."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def connect():
    """Borrow a connection from the pool. Use it with `with`."""
    return _get_pool().connection()


def get_consent(subject_id: str) -> str | None:
    """What our record says: 'granted', 'denied', 'paused', or None.

    None means we have no record for this subject at all.

    This is the point of the whole file. abc.md:187 says the API
    "verifies ... consent state" - and you cannot verify a claim against
    nothing. The caller tells us what they believe; this tells us what is
    true.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT state FROM consent WHERE subject_id = %s",
            (subject_id,),
        ).fetchone()
    return row[0] if row else None


def save_event(event_id: str, event: dict, service_id: str) -> None:
    """Write one accepted event.

    `event` is the validated request body as a dict.
    """
    expires_at = datetime.now(timezone.utc) + RAW_EVENT_RETENTION

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO ingested_event (
                event_id, subject_id, service_id, event_type, surface,
                locale, schema_version, source_event_id, idempotency_key,
                occurred_at, expires_at, content
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                event_id,
                event["subject_id"],
                service_id,
                event["event_type"],
                event["surface"],
                event["locale"],
                event["schema_version"],
                event["source_event_id"],
                event["idempotency_key"],
                event["occurred_at"],
                expires_at,
                # Untrusted free text (abc.md:134). Stored as data only.
                event.get("content"),
            ),
        )


def count_events(subject_id: str) -> int:
    """How many events we hold for one subject. Used by tests."""
    with connect() as conn:
        row = conn.execute(
            "SELECT count(*) FROM ingested_event WHERE subject_id = %s",
            (subject_id,),
        ).fetchone()
    return row[0]


def delete_events(subject_id: str) -> None:
    """Remove one subject's events. Used by tests to clean up."""
    with connect() as conn:
        conn.execute(
            "DELETE FROM ingested_event WHERE subject_id = %s", (subject_id,)
        )


# A subject's region and age band, for memory/retention_rules.py.
def get_region_and_age(subject_id: str) -> tuple[str | None, str]:
    """No record means nothing is known: no region, treated as an adult."""
    with connect() as conn:
        row = conn.execute(
            "SELECT region, age_band FROM consent WHERE subject_id = %s",
            (subject_id,),
        ).fetchone()
    return (row[0], row[1]) if row else (None, "adult")


# Record a subject's region and age band, when they are given.
def set_region_and_age(subject_id: str, region: str | None, age_band: str | None) -> None:
    with connect() as conn:
        if region is not None:
            conn.execute("UPDATE consent SET region = %s WHERE subject_id = %s",
                         (region.upper(), subject_id))
        if age_band is not None:
            conn.execute("UPDATE consent SET age_band = %s WHERE subject_id = %s",
                         (age_band, subject_id))


# Every subject with a consent record, for the console's subject picker.
def list_subjects() -> list[dict]:
    """Only the id, consent, when it was set, experiment group, region and
    age band - nothing else is kept about a person (abc.md:53, purpose limitation and minimization).
    The golden set's own test subjects are left out; they belong to the
    quality run, not to anyone an operator would pick.
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT c.subject_id, c.state, c.updated_at, e.cohort, c.region, c.age_band"
            " FROM consent c"
            " LEFT JOIN experiment_cohort e ON e.subject_id = c.subject_id"
            " WHERE c.subject_id NOT LIKE 'golden\\_%%'"
            " ORDER BY c.subject_id"
        ).fetchall()
    # No allocation means memory_enabled, the same rule as cohort_of().
    return [
        {"subject_id": subject_id, "consent": state, "updated_at": updated_at,
         "cohort": cohort or "memory_enabled", "region": region, "age_band": age_band}
        for subject_id, state, updated_at, cohort, region, age_band in rows
    ]


def set_consent(subject_id: str, state: str) -> None:
    """Set a subject's consent. Used by tests and by seeding."""
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO consent (subject_id, state) VALUES (%s, %s)
            ON CONFLICT (subject_id)
            DO UPDATE SET state = EXCLUDED.state, updated_at = now()
            """,
            (subject_id, state),
        )


# --- Audit trail ----------------------------------------------------------

def record_audit(
    action: str,
    subject_id: str,
    service_id: str,
    outcome: str,
    correlation_id: str,
    reason: str | None = None,
    event_id: str | None = None,
    memory_id: str | None = None,
) -> None:
    """Write one line saying what happened.

    abc.md:460 scores the backend on audit events. abc.md:322 says logs
    carry identifiers and outcomes, not private content - so there is no
    parameter here for what the listener played or said, by design.
    """
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO audit_log
                (action, subject_id, service_id, outcome,
                 correlation_id, reason, event_id, memory_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (action, subject_id, service_id, outcome,
             correlation_id, reason, event_id, memory_id),
        )


def get_audit(subject_id: str) -> list[dict]:
    """Read one subject's audit trail, newest first."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT action, outcome, reason, event_id, correlation_id, service_id,
                   memory_id
            FROM audit_log WHERE subject_id = %s ORDER BY id DESC
            """,
            (subject_id,),
        ).fetchall()
    keys = ("action", "outcome", "reason", "event_id", "correlation_id",
            "service_id", "memory_id")
    return [dict(zip(keys, row)) for row in rows]


def delete_audit(subject_id: str) -> None:
    """Remove one subject's audit lines. Used by tests."""
    with connect() as conn:
        conn.execute("DELETE FROM audit_log WHERE subject_id = %s", (subject_id,))


# --- Retention ------------------------------------------------------------

def delete_expired_events() -> int:
    """Delete raw events past their expiry, and say how many went.

    abc.md:109 - raw events expire on their own clock. Storing expires_at
    is not enough; something has to actually remove them.
    """
    with connect() as conn:
        result = conn.execute(
            "DELETE FROM ingested_event WHERE expires_at < now()"
        )
        return result.rowcount


# --- Metrics --------------------------------------------------------------

def ingestion_metrics() -> dict:
    """Every number the Overview screen shows.

    abc.md:339 lists seven: service health, ingestion lag, retrieval SLO,
    fallback rate, quality metrics, experiment status and deletion
    backlog. Health is its own endpoint; the other six are here.

    The event counts and the rejection rate come from the audit table we
    already write, so there is no separate counter to keep in step. The
    rest come from the tables migration 006 adds.
    """
    with connect() as conn:
        outcomes = dict(
            conn.execute(
                "SELECT outcome, count(*) FROM audit_log GROUP BY outcome"
            ).fetchall()
        )

        reasons = dict(
            conn.execute(
                "SELECT reason, count(*) FROM audit_log"
                " WHERE reason IS NOT NULL GROUP BY reason"
            ).fetchall()
        )

        # Ingestion lag: how long ago the newest event arrived. If this
        # grows, events have stopped coming in.
        lag = conn.execute(
            "SELECT extract(epoch FROM now() - max(received_at))"
            " FROM ingested_event"
        ).fetchone()[0]

        stored = conn.execute("SELECT count(*) FROM ingested_event").fetchone()[0]

    accepted = outcomes.get("accepted", 0)
    rejected = outcomes.get("rejected", 0)
    total = accepted + rejected + outcomes.get("duplicate", 0)

    return {
        "events": {
            "accepted": accepted,
            "rejected": rejected,
            "duplicate": outcomes.get("duplicate", 0),
            "stored": stored,
        },
        "rejection_rate": round(rejected / total, 4) if total else 0.0,
        "rejections_by_reason": reasons,
        "ingestion_lag_seconds": round(lag, 1) if lag is not None else None,

        # The four abc.md:339 asks for that used to have no source.
        "retrieval_slo": retrieval_slo(),
        "fallback": fallback_rate(),
        "deletion_backlog": deletion_backlog(),
        "experiment": experiment_status(),
        "quality": latest_golden_run(),
    }


# --- The Overview screen's remaining numbers -------------------------------
#
# abc.md:143 - "Monitor ingestion lag, write failures, retrieval latency,
#              cache effectiveness, fallback rate, deletion backlog, and
#              policy rejection rate."


# Record how long one request took. Called for every request.
def record_latency(route: str, duration_ms: float, status_code: int) -> None:
    """One row per request, because a percentile needs the individual
    durations - abc.md:170 sets a P95 budget, and P95 cannot be derived
    from a running average."""
    with connect() as conn:
        conn.execute(
            "INSERT INTO request_latency (route, duration_ms, status_code)"
            " VALUES (%s, %s, %s)",
            (route, duration_ms, status_code),
        )


# The latency percentiles for the retrieval path, against the budget.
def retrieval_slo(window_minutes: int = 60) -> dict:
    """abc.md:170 - "P95 retrieval and context composition should remain
    within a 250 ms service budget for the pilot."

    Only the two routes that budget names are measured. Mixing the write
    path in would flatter the number, because accepting an event is much
    faster than searching a graph.
    """
    routes = ("POST /v1/memories/search", "POST /v1/context/compose")
    with connect() as conn:
        row = conn.execute(
            "SELECT count(*),"
            "       percentile_disc(0.50) WITHIN GROUP (ORDER BY duration_ms),"
            "       percentile_disc(0.95) WITHIN GROUP (ORDER BY duration_ms),"
            "       percentile_disc(0.99) WITHIN GROUP (ORDER BY duration_ms)"
            " FROM request_latency"
            " WHERE route = ANY(%s)"
            "   AND recorded_at > now() - make_interval(mins => %s)",
            (list(routes), window_minutes),
        ).fetchone()

    samples, p50, p95, p99 = row
    return {
        "budget_ms": 250,          # abc.md:170
        "window_minutes": window_minutes,
        "samples": samples,
        "p50_ms": round(p50, 1) if p50 is not None else None,
        "p95_ms": round(p95, 1) if p95 is not None else None,
        "p99_ms": round(p99, 1) if p99 is not None else None,
        # The only judgement this function makes: is the budget being met?
        "within_budget": (p95 is not None and p95 <= 250) if samples else None,
    }


# How often we answered without memory, and why.
def fallback_rate(window_minutes: int = 60) -> dict:
    """abc.md:143 - the fallback rate is a monitored number.

    A fallback is normal behaviour, not an error: abc.md:167 requires
    retrieval to "fail open to a non-personalized response". The reasons
    are returned alongside the rate because a high rate caused by paused
    consent is a different problem from one caused by an unhealthy graph.
    """
    with connect() as conn:
        total, fell_back = conn.execute(
            "SELECT count(*), count(*) FILTER (WHERE fell_back)"
            " FROM fallback_event"
            " WHERE recorded_at > now() - make_interval(mins => %s)",
            (window_minutes,),
        ).fetchone()

        reasons = dict(
            conn.execute(
                "SELECT reason, count(*) FROM fallback_event"
                " WHERE fell_back AND reason IS NOT NULL"
                "   AND recorded_at > now() - make_interval(mins => %s)"
                " GROUP BY reason",
                (window_minutes,),
            ).fetchall()
        )

    return {
        "window_minutes": window_minutes,
        "requests": total,
        "fell_back": fell_back,
        "rate": round(fell_back / total, 4) if total else 0.0,
        "by_reason": reasons,
    }


# Record whether one context request used memory.
def record_fallback(
    subject_id: str, fell_back: bool, reason: str | None, correlation_id: str
) -> None:
    """Called once per context composition, so the rate above has a
    denominator as well as a numerator."""
    with connect() as conn:
        conn.execute(
            "INSERT INTO fallback_event"
            " (subject_id, fell_back, reason, correlation_id)"
            " VALUES (%s, %s, %s, %s)",
            (subject_id, fell_back, reason, correlation_id),
        )


# Deletion jobs that have not finished clearing every store.
def deletion_backlog() -> dict:
    """abc.md:143 - the deletion backlog is monitored, and abc.md:361
    blocks release outright if deletion propagation is incomplete.

    So "oldest_pending_seconds" matters as much as the count: one job stuck
    for an hour is a release blocker, while ten jobs a second old are not.
    """
    with connect() as conn:
        by_status = dict(
            conn.execute(
                "SELECT status, count(*) FROM deletion_job GROUP BY status"
            ).fetchall()
        )

        oldest = conn.execute(
            "SELECT extract(epoch FROM now() - min(requested_at))"
            " FROM deletion_job WHERE status IN ('pending', 'partial', 'failed')"
        ).fetchone()[0]

    unfinished = (
        by_status.get("pending", 0)
        + by_status.get("partial", 0)
        + by_status.get("failed", 0)
    )
    return {
        "unfinished": unfinished,
        "completed": by_status.get("completed", 0),
        "by_status": by_status,
        "oldest_pending_seconds": round(oldest, 1) if oldest is not None else None,
    }


# Which cohort each subject is in, and how the split came out.
def experiment_status(experiment: str = "pilot_memory_uplift") -> dict:
    """abc.md:146 - "Support memory-enabled and memory-disabled experiments
    under consistent cohort allocation and guardrails."

    Consistent means a subject keeps its cohort, which is why the
    allocation is stored rather than decided per request.
    """
    with connect() as conn:
        counts = dict(
            conn.execute(
                "SELECT cohort, count(*) FROM experiment_cohort"
                " WHERE experiment = %s GROUP BY cohort",
                (experiment,),
            ).fetchall()
        )

    enabled = counts.get("memory_enabled", 0)
    disabled = counts.get("memory_disabled", 0)
    return {
        "experiment": experiment,
        "memory_enabled": enabled,
        "memory_disabled": disabled,
        "subjects": enabled + disabled,
        # Without a baseline arm there is nothing to compare against, which
        # is what abc.md:344 needs for a side-by-side comparison.
        "has_baseline": disabled > 0,
    }


# Which cohort one subject is in.
def cohort_of(subject_id: str, experiment: str = "pilot_memory_uplift") -> str:
    """A subject with no allocation is treated as memory_enabled, which is
    the normal product behaviour - the experiment opts subjects OUT of
    memory, never into it."""
    with connect() as conn:
        row = conn.execute(
            "SELECT cohort FROM experiment_cohort"
            " WHERE subject_id = %s AND experiment = %s",
            (subject_id, experiment),
        ).fetchone()
    return row[0] if row else "memory_enabled"


def get_event(event_id: str, subject_id: str) -> dict | None:
    """Read one event back, for extraction.

    Takes the subject as well as the id: every read is subject-scoped
    (abc.md:110, "subject isolation at the query boundary"), so one
    subject can never read another's event even knowing its id.
    """
    with connect() as conn:
        row = conn.execute(
            """
            SELECT event_id, subject_id, event_type, surface, locale,
                   occurred_at, content
            FROM ingested_event
            WHERE event_id = %s AND subject_id = %s
            """,
            (event_id, subject_id),
        ).fetchone()

    if row is None:
        return None

    keys = ("event_id", "subject_id", "event_type", "surface", "locale",
            "occurred_at", "content")
    return dict(zip(keys, row))


# --- Feedback -------------------------------------------------------------

def negative_feedback(subject_id: str) -> set[str]:
    """Memory ids this subject marked unhelpful.

    abc.md:124 lists negative feedback as a ranking signal. Until
    POST /v1/feedback is real this reads the audit trail, which already
    records rejections against a memory id.
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT memory_id FROM audit_log "
            "WHERE subject_id = %s AND action = 'feedback.negative' "
            "AND memory_id IS NOT NULL",
            (subject_id,),
        ).fetchall()
    return {r[0] for r in rows}


# --- Golden-set runs -------------------------------------------------------
#
# abc.md:148 - "Maintain golden sets for explicit preference, temporal
#              change, contradiction, multilingual interaction, sparse
#              history, and malicious stored text."
# abc.md:361 - release is blocked if provenance falls below threshold, so a
#              run's score is a gate and has to be stored, not printed.


# Open a run and return its id.
def start_golden_run(run_id: str, total_cases: int) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO golden_run (run_id, total_cases) VALUES (%s, %s)",
            (run_id, total_cases),
        )


# Record what happened to one case.
def record_golden_case(
    run_id: str,
    case_id: str,
    category: str,
    locale: str | None,
    passed: bool,
    failure_reason: str | None,
    expected_top: int,
    matched_top: int,
    prohibited_leaked: int,
) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO golden_case_result"
            " (run_id, case_id, category, locale, passed, failure_reason,"
            "  expected_top, matched_top, prohibited_leaked)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (run_id, case_id, category, locale, passed, failure_reason,
             expected_top, matched_top, prohibited_leaked),
        )


# Close a run with its scores.
def finish_golden_run(
    run_id: str,
    passed: int,
    failed: int,
    precision_at_k: float,
    contradiction_rate: float,
    provenance_completeness: float,
) -> None:
    """abc.md:82 - "precision at the top of the retrieved set, not just
    retrieval recall. One wrong memory can be more damaging than three
    missing ones." Which is why precision is stored separately from the
    pass count."""
    with connect() as conn:
        conn.execute(
            "UPDATE golden_run SET finished_at = now(), status = 'completed',"
            " passed = %s, failed = %s, precision_at_k = %s,"
            " contradiction_rate = %s, provenance_completeness = %s"
            " WHERE run_id = %s",
            (passed, failed, precision_at_k, contradiction_rate,
             provenance_completeness, run_id),
        )


# The most recent run, for the Overview screen's quality metrics.
def latest_golden_run() -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT run_id, started_at, finished_at, status, total_cases,"
            "       passed, failed, precision_at_k, contradiction_rate,"
            "       provenance_completeness"
            " FROM golden_run ORDER BY started_at DESC LIMIT 1"
        ).fetchone()

    if row is None:
        return None
    return {
        "run_id": row[0],
        "started_at": row[1].isoformat() if row[1] else None,
        "finished_at": row[2].isoformat() if row[2] else None,
        "status": row[3],
        "total_cases": row[4],
        "passed": row[5],
        "failed": row[6],
        "precision_at_k": row[7],
        "contradiction_rate": row[8],
        "provenance_completeness": row[9],
    }


# Every run, newest first, for the Quality review screen.
def golden_runs(limit: int = 20) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT run_id, started_at, finished_at, status, total_cases,"
            "       passed, failed, precision_at_k, contradiction_rate,"
            "       provenance_completeness"
            " FROM golden_run ORDER BY started_at DESC LIMIT %s",
            (limit,),
        ).fetchall()

    return [
        {
            "run_id": r[0],
            "started_at": r[1].isoformat() if r[1] else None,
            "finished_at": r[2].isoformat() if r[2] else None,
            "status": r[3],
            "total_cases": r[4],
            "passed": r[5],
            "failed": r[6],
            "precision_at_k": r[7],
            "contradiction_rate": r[8],
            "provenance_completeness": r[9],
        }
        for r in rows
    ]


# The cases of one run, so a failure can be traced to the case that caused it.
def golden_cases(run_id: str) -> list[dict]:
    """abc.md:344 asks for "failure clusters", which is these rows grouped
    by category - so the category travels with every case."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT case_id, category, locale, passed, failure_reason,"
            "       expected_top, matched_top, prohibited_leaked"
            " FROM golden_case_result WHERE run_id = %s"
            " ORDER BY passed, category, case_id",
            (run_id,),
        ).fetchall()

    return [
        {
            "case_id": r[0],
            "category": r[1],
            "locale": r[2],
            "passed": r[3],
            "failure_reason": r[4],
            "expected_top": r[5],
            "matched_top": r[6],
            "prohibited_leaked": r[7],
        }
        for r in rows
    ]
