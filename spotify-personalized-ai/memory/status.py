"""Why this file exists
=====================

Answers "is every part connected?" in one call, so a deployment can be
checked without reading its logs.

abc.md §5.5 Deployment - "health checks". /health only says the API process
is alive. This checks each store the system depends on, and whether the
worker is running:

    postgres   a simple query
    redis      PING
    neo4j      a connectivity check
    kafka      the cluster answers and both topics exist
    worker     the in-API worker thread is alive (RUN_WORKER_IN_API)

It never returns an address, a username or a password - only "ok", or the
kind of error (for example "NoBrokersAvailable" or "AuthenticationFailed"),
which is enough to know what to fix.

Where it is used
----------------
memory/api.py - GET /health/stores.
"""

import threading


# Run one check; "ok", or the error's type and a short, secret-free hint.
def _check(fn) -> str:
    try:
        fn()
        return "ok"
    except LookupError as exc:
        # Our own message (a missing topic name) - safe to show.
        return f"error: {exc}"
    except Exception as exc:  # noqa: BLE001 - report, never raise
        # Only the type: provider messages can contain addresses.
        return f"error: {type(exc).__name__}"


def _postgres():
    from memory import db

    with db.connect() as conn:
        conn.execute("SELECT 1").fetchone()


def _redis():
    from memory import cache

    cache.client().ping()


def _neo4j():
    from memory import graph

    graph.driver().verify_connectivity()


def _kafka():
    from kafka import KafkaConsumer

    from memory import queue

    consumer = KafkaConsumer(
        bootstrap_servers=queue.BOOTSTRAP,
        request_timeout_ms=10000,
        **queue.connection_settings(),
    )
    try:
        topics = consumer.topics()
    finally:
        consumer.close()
    missing = {queue.TOPIC, queue.DEAD_LETTER_TOPIC} - set(topics)
    if missing:
        raise LookupError("topic missing: " + ", ".join(sorted(missing)))


# Is the in-API worker thread alive?
def worker_state() -> str:
    from memory import worker

    alive = any(t.name == "memory-worker" and t.is_alive() for t in threading.enumerate())
    if alive:
        return "running inside the API"
    if worker.runs_in_api():
        return "expected but not running"
    return "not in this process (RUN_WORKER_IN_API is off)"


# Every check together.
def summary() -> dict:
    stores = {
        "postgres": _check(_postgres),
        "redis": _check(_redis),
        "neo4j": _check(_neo4j),
        "kafka": _check(_kafka),
    }
    return {
        "status": "ok" if all(v == "ok" for v in stores.values()) else "degraded",
        "stores": stores,
        "worker": worker_state(),
    }
