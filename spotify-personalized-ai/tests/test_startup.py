"""Why this file exists
=====================

A fresh deployment has empty stores. memory/startup.py creates the tables,
constraints and vector index when the API starts, so no manual step is
needed. These check it runs on startup, is safe to repeat, and cannot stop
the API from starting when a store is down.
"""

import pytest
from fastapi.testclient import TestClient

from memory import startup
from memory.api import app


# Starting the API runs the store preparation once.
def test_the_api_prepares_the_stores_when_it_starts(monkeypatch):
    calls = []
    monkeypatch.setattr(startup, "prepare_stores", lambda: calls.append(1))
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
    assert calls == [1]


# Every migration can run again on a database that already has it.
def test_migrations_are_safe_to_repeat():
    first = startup.apply_migrations()
    second = startup.apply_migrations()
    assert first == second and "008_accounts.sql" in first


# A store that is down is logged, not raised.
def test_a_store_that_is_down_does_not_stop_startup(monkeypatch):
    def down():
        raise ConnectionError("database unreachable")

    monkeypatch.setattr(startup, "apply_migrations", down)
    monkeypatch.setattr(startup, "prepare_graph", down)
    startup.prepare_stores()   # must not raise


# --- The worker inside the API (free hosts) ---------------------------------

from memory import worker  # noqa: E402


def test_the_worker_stays_out_of_the_api_by_default(monkeypatch):
    monkeypatch.delenv("RUN_WORKER_IN_API", raising=False)
    started = []
    monkeypatch.setattr(startup, "apply_migrations", lambda: [])
    monkeypatch.setattr(startup, "prepare_graph", lambda: None)
    monkeypatch.setattr(worker, "start_in_background", lambda: started.append(1))
    startup.prepare_stores()
    assert started == []


def test_the_setting_starts_the_worker_inside_the_api(monkeypatch):
    monkeypatch.setenv("RUN_WORKER_IN_API", "true")
    started = []
    monkeypatch.setattr(startup, "apply_migrations", lambda: [])
    monkeypatch.setattr(startup, "prepare_graph", lambda: None)
    monkeypatch.setattr(worker, "start_in_background", lambda: started.append(1))
    startup.prepare_stores()
    assert started == [1]


# A value typed with quotes or spaces in a dashboard still counts.
@pytest.mark.parametrize("value", ["true", "True", '"true"', "'true'", " yes ", "1", "on"])
def test_the_setting_accepts_how_people_type_it(monkeypatch, value):
    monkeypatch.setenv("RUN_WORKER_IN_API", value)
    assert worker.runs_in_api()


@pytest.mark.parametrize("value", ["", "false", "no", "0"])
def test_the_setting_is_off_otherwise(monkeypatch, value):
    monkeypatch.setenv("RUN_WORKER_IN_API", value)
    assert not worker.runs_in_api()
