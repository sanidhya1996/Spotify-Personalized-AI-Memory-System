"""Why this file exists
=====================

abc.md §5.4 - monitor "write failures, ... cache effectiveness, ... and
policy rejection rate."

Checks memory/monitoring.py counts each one from the records it reads, and
that GET /metrics returns them for the Overview screen.
"""

from fastapi.testclient import TestClient

from memory import db, monitoring
from memory.api import app
from memory.auth import mint_token

client = TestClient(app)
AUTH = {"Authorization": f"Bearer {mint_token('user_005', 'memory-console')}"}


# Write one audit row for user_005 (the conftest clears it after each test).
def audit(action, outcome):
    db.record_audit(action=action, subject_id="user_005", service_id="test",
                    outcome=outcome, correlation_id="cid_test")


def test_a_worker_failure_counts_as_a_write_failure():
    before = monitoring.write_failures()["failed"]
    audit("processor.failed", "failed")
    assert monitoring.write_failures()["failed"] == before + 1


def test_a_duplicate_counts_as_a_cache_hit():
    before = monitoring.cache_effectiveness()
    audit("event.duplicate", "duplicate")
    after = monitoring.cache_effectiveness()
    assert after["hits"] == before["hits"] + 1
    assert after["lookups"] == before["lookups"] + 1


def test_an_excluded_memory_counts_as_a_policy_rejection():
    before = monitoring.policy_rejection_rate()["excluded"]
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO trace_decision (trace_id, subject_id, stage, memory_id, decision)"
            " VALUES ('cid_monitoring_test', 'user_005', 'policy', 'mem_x', 'excluded')"
        )
    try:
        assert monitoring.policy_rejection_rate()["excluded"] == before + 1
    finally:
        with db.connect() as conn:
            conn.execute("DELETE FROM trace_decision WHERE trace_id = 'cid_monitoring_test'")


def test_metrics_returns_all_three():
    body = client.get("/metrics", headers=AUTH).json()
    assert {"write_failures", "cache_effectiveness", "policy_rejection"} <= set(body)
    assert 0.0 <= body["policy_rejection"]["rate"] <= 1.0


def test_failure_reasons_are_short_and_secret_free():
    from memory import processor
    exc = RuntimeError("ModelUnavailable: 503 UNAVAILABLE key=AIzaSECRET at https://host")
    assert processor.failure_reason(exc) == "RuntimeError: 503"


def test_reasons_are_listed():
    db.record_audit(action="processor.failed", subject_id="user_005", service_id="test",
                    outcome="failed", correlation_id="cid_test",
                    reason="ModelUnavailable: 503")
    reasons = monitoring.write_failures()["reasons"]
    assert {"kind": "failed", "reason": "ModelUnavailable: 503", "count": 1} in reasons


def test_a_timeout_and_a_bad_reply_are_named():
    from memory import processor
    assert processor.failure_reason(RuntimeError("ReadTimeout: The read operation timed out")) == "RuntimeError: timeout"
    assert processor.failure_reason(RuntimeError("model returned invalid JSON: x")) == "RuntimeError: invalid JSON"
