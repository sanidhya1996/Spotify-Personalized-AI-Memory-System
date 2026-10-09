"""Why this file exists
=====================

The listener login (memory/accounts.py, POST /auth/signup, POST /auth/login).

Checks the three things it exists for:
  - one person cannot take an id that already exists
  - only the right password gets a pass, and guessing is limited
  - a pass opens that listener's own data and nobody else's
"""

import pytest
from fastapi.testclient import TestClient

from memory import accounts, cache, db
from memory.api import app

client = TestClient(app)
NEW = "login_test_listener"
PASSWORD = "a-good-password"


@pytest.fixture(autouse=True)
def forget_new_listener():
    def remove():
        db.delete_audit(NEW)
        accounts.clear_failures(NEW)
        accounts.clear_failures("user_002")
        with db.connect() as conn:
            conn.execute("DELETE FROM account WHERE subject_id = %s", (NEW,))
            conn.execute("DELETE FROM consent WHERE subject_id = %s", (NEW,))

    remove()
    yield
    remove()


def signup(**extra):
    return client.post("/auth/signup",
                       json={"subject_id": NEW, "password": PASSWORD, **extra})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


# --- Sign up ----------------------------------------------------------------

def test_signup_creates_the_account_and_switches_memory_on():
    r = signup(region="DE", age_band="under_18")
    assert r.status_code == 200
    assert r.json()["subject_id"] == NEW and r.json()["token"]
    assert db.get_consent(NEW) == "granted"
    assert db.get_region_and_age(NEW) == ("DE", "under_18")


def test_an_id_can_only_be_signed_up_once():
    assert signup().status_code == 200
    assert signup().status_code == 409


def test_nobody_can_sign_up_as_an_existing_user():
    r = client.post("/auth/signup", json={"subject_id": "user_002", "password": PASSWORD})
    assert r.status_code == 409


def test_a_short_password_or_bad_id_is_refused():
    assert client.post("/auth/signup",
                       json={"subject_id": NEW, "password": "short"}).status_code == 422
    assert client.post("/auth/signup",
                       json={"subject_id": "Bad Id!", "password": PASSWORD}).status_code == 422


def test_the_password_is_never_stored_as_text():
    signup()
    with db.connect() as conn:
        stored = conn.execute("SELECT password_hash FROM account WHERE subject_id = %s",
                              (NEW,)).fetchone()[0]
    assert PASSWORD not in stored and stored.startswith("scrypt$")


# --- Log in -----------------------------------------------------------------

def test_the_right_password_logs_in():
    signup()
    r = client.post("/auth/login", json={"subject_id": NEW, "password": PASSWORD})
    assert r.status_code == 200 and r.json()["token"]


def test_a_wrong_password_and_an_unknown_id_look_the_same():
    signup()
    wrong = client.post("/auth/login", json={"subject_id": NEW, "password": "nope-nope"})
    unknown = client.post("/auth/login", json={"subject_id": "nobody_here", "password": "x"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"]["message"] == unknown.json()["detail"]["message"]


def test_five_wrong_passwords_pause_logins():
    signup()
    for _ in range(accounts.MAX_FAILED_LOGINS):
        client.post("/auth/login", json={"subject_id": NEW, "password": "wrong-one"})
    r = client.post("/auth/login", json={"subject_id": NEW, "password": PASSWORD})
    assert r.status_code == 429


def test_test_users_get_no_login_without_a_demo_password(monkeypatch):
    monkeypatch.delenv("DEMO_PASSWORD", raising=False)
    assert accounts.ensure_demo_accounts() == 0


# --- What a pass opens -------------------------------------------------------

def test_a_pass_opens_your_own_data():
    token = signup().json()["token"]
    r = client.get("/v1/consent", params={"subject_id": NEW}, headers=bearer(token))
    assert r.status_code == 200 and r.json()["state"] == "granted"


def test_a_pass_never_opens_somebody_elses_data():
    token = signup().json()["token"]
    r = client.post("/v1/memories/search", headers=bearer(token),
                    json={"subject_id": "user_001", "intent": "music"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "SUBJECT_MISMATCH"


def test_no_pass_or_a_fake_pass_opens_nothing():
    assert client.get("/v1/consent", params={"subject_id": NEW}).status_code == 401
    r = client.get("/v1/consent", params={"subject_id": NEW}, headers=bearer("not.a.token"))
    assert r.status_code == 401


def test_login_attempts_are_audited_without_the_password():
    signup()
    client.post("/auth/login", json={"subject_id": NEW, "password": "wrong-one"})
    rows = db.get_audit(NEW)
    assert {"account.created", "login.failed"} <= {row["action"] for row in rows}
    assert all(PASSWORD not in str(row) for row in rows)
