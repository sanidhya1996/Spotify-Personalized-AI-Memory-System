"""Why this file exists
=====================

abc.md §5.4 - "Apply retention by memory type, geography, age-related
policy, and consent state."

Checks memory/retention_rules.py and data/retention_rules.yaml: geography and
age can only shorten retention, the strictest rule wins, and the subject's
region and age band are taken from what PATCH /v1/consent stored.
"""

import pytest
from fastapi.testclient import TestClient

from memory import db, policy, retention_rules
from memory.api import app
from memory.auth import mint_token

client = TestClient(app)
SUBJECT = "retention_rules_test"
AUTH = {"Authorization": f"Bearer {mint_token(SUBJECT, 'chat-surface')}"}


@pytest.fixture(autouse=True)
def forget_subject():
    def remove():
        db.delete_audit(SUBJECT)
        with db.connect() as conn:
            conn.execute("DELETE FROM consent WHERE subject_id = %s", (SUBJECT,))

    remove()
    yield
    remove()


# Create the test subject with a region and age band, through the API.
def create(region=None, age_band=None):
    body = {"subject_id": SUBJECT, "state": "granted"}
    if region:
        body["region"] = region
    if age_band:
        body["age_band"] = age_band
    r = client.patch("/v1/consent", json=body, headers=AUTH)
    assert r.status_code == 200, r.json()


# --- The rule itself --------------------------------------------------------

def test_nothing_changes_for_an_adult_outside_strict_regions():
    assert retention_rules.limit(730, "IN", "adult") == (730, [])
    assert retention_rules.limit(730, None, "adult") == (730, [])


def test_a_strict_region_caps_retention():
    days, reasons = retention_rules.limit(730, "de", "adult")
    assert days == 365
    assert reasons == ["region DE: at most 365 days"]


def test_under_18_caps_retention_hardest():
    days, reasons = retention_rules.limit(730, "DE", "under_18")
    assert days == 30
    assert len(reasons) == 2


def test_a_rule_never_makes_retention_longer():
    assert retention_rules.limit(10, "DE", "under_18") == (10, [])


# --- Applied to real memories ----------------------------------------------

def test_an_exclusion_for_a_listener_in_germany_is_kept_365_days():
    create(region="DE")
    assert policy.classify("exclusion", subject_id=SUBJECT).retention_days == 365


def test_any_memory_of_an_under_18_listener_is_kept_30_days():
    create(age_band="under_18")
    assert policy.classify("explicit_preference", subject_id=SUBJECT).retention_days == 30


def test_without_a_subject_only_the_type_rule_applies():
    assert policy.classify("exclusion").retention_days == 730


def test_region_and_age_are_stored_and_listed():
    create(region="gb", age_band="under_18")
    assert db.get_region_and_age(SUBJECT) == ("GB", "under_18")


def test_a_bad_region_is_refused():
    r = client.patch("/v1/consent", headers=AUTH,
                     json={"subject_id": SUBJECT, "state": "granted", "region": "Germany"})
    assert r.status_code == 422
