"""Why this file exists
=====================

GET /health/stores (memory/status.py) checks every store and the worker, and
must never leak an address or a credential.
"""

from fastapi.testclient import TestClient

from memory import status
from memory.api import app

client = TestClient(app)


def test_every_local_store_reports_ok():
    body = client.get("/health/stores").json()
    assert body["stores"] == {"postgres": "ok", "redis": "ok", "neo4j": "ok", "kafka": "ok"}
    assert body["status"] == "ok"


def test_a_failure_shows_only_the_error_type():
    def broken():
        raise ConnectionError("cannot reach secret-host.example:9092 as user bob")

    assert status._check(broken) == "error: ConnectionError"


def test_a_missing_topic_is_named():
    def missing():
        raise LookupError("topic missing: interaction-events")

    assert status._check(missing) == "error: topic missing: interaction-events"


def test_the_worker_state_is_reported(monkeypatch):
    monkeypatch.setenv("RUN_WORKER_IN_API", "true")
    assert status.worker_state() in ("running inside the API", "expected but not running")
    monkeypatch.setenv("RUN_WORKER_IN_API", "false")
    assert status.worker_state() in ("running inside the API",
                                     "not in this process (RUN_WORKER_IN_API is off)")
