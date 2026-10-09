"""POST /v1/events — the real behaviours.

Runs against the Postgres and Redis configured in .env.
"""

from fastapi.testclient import TestClient

from memory import cache, db
from memory.api import app
from memory.auth import mint_token

client = TestClient(app)

AUTH = {"Authorization": f"Bearer {mint_token('user_001', 'chat-surface')}"}


def valid_event(**overrides):
    body = {
        "schema_version": "1.0",
        "subject_id": "user_001",
        "event_type": "playback",
        "surface": "player",
        "locale": "en-US",
        "occurred_at": "2026-09-23T10:00:00Z",
        "consent_state": "granted",
        "source_event_id": "src_1",
        "idempotency_key": "key_1",
    }
    body.update(overrides)
    return body


# 1. Validate ---------------------------------------------------------------

def test_valid_event_is_accepted():
    r = client.post("/v1/events", json=valid_event(), headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] is True
    assert body["duplicate"] is False
    assert body["event_id"].startswith("evt_")
    assert db.count_events("user_001") == 1


def test_missing_subject_id_is_rejected():
    body = valid_event()
    del body["subject_id"]
    r = client.post("/v1/events", json=body, headers=AUTH)
    assert r.status_code == 422
    assert db.count_events("user_001") == 0


def test_empty_subject_id_is_rejected():
    r = client.post("/v1/events", json=valid_event(subject_id=""), headers=AUTH)
    assert r.status_code == 422


def test_unknown_surface_is_rejected():
    r = client.post("/v1/events", json=valid_event(surface="teleport"), headers=AUTH)
    assert r.status_code == 422
    assert db.count_events("user_001") == 0


def test_unknown_event_type_is_rejected():
    r = client.post("/v1/events", json=valid_event(event_type="teleport"), headers=AUTH)
    assert r.status_code == 422
    assert db.count_events("user_001") == 0


def test_missing_source_event_id_is_rejected():
    body = valid_event()
    del body["source_event_id"]
    r = client.post("/v1/events", json=body, headers=AUTH)
    assert r.status_code == 422
    assert db.count_events("user_001") == 0


# 2. Schema version ---------------------------------------------------------

def test_unsupported_schema_version_is_rejected():
    r = client.post("/v1/events", json=valid_event(schema_version="0.9"), headers=AUTH)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "UNSUPPORTED_SCHEMA_VERSION"
    assert db.count_events("user_001") == 0


def test_missing_schema_version_is_rejected():
    body = valid_event()
    del body["schema_version"]
    r = client.post("/v1/events", json=body, headers=AUTH)
    assert r.status_code == 422
    assert db.count_events("user_001") == 0


# 3. Consent — our record decides, not the caller's claim -------------------
#
# abc.md:187 — the ingestion API "verifies ... consent state".

def test_denied_consent_in_our_record_is_refused():
    db.set_consent("user_001", "denied")
    r = client.post("/v1/events", json=valid_event(), headers=AUTH)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "CONSENT_DENIED"
    assert db.count_events("user_001") == 0


def test_paused_consent_in_our_record_is_refused():
    db.set_consent("user_001", "paused")
    r = client.post("/v1/events", json=valid_event(), headers=AUTH)
    assert r.status_code == 403
    assert db.count_events("user_001") == 0


def test_caller_cannot_claim_consent_it_does_not_have():
    """The whole point: the body says granted, our record says denied."""
    db.set_consent("user_001", "denied")
    r = client.post(
        "/v1/events", json=valid_event(consent_state="granted"), headers=AUTH
    )
    assert r.status_code == 403
    assert db.count_events("user_001") == 0


def test_unknown_subject_has_no_consent_record():
    """No record means no permission — we never assume it."""
    auth = {"Authorization": f"Bearer {mint_token('user_999', 'chat-surface')}"}
    r = client.post(
        "/v1/events", json=valid_event(subject_id="user_999"), headers=auth
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "CONSENT_DENIED"


# 4. Idempotency ------------------------------------------------------------

def test_same_key_twice_stores_one_event():
    first = client.post("/v1/events", json=valid_event(), headers=AUTH)
    second = client.post("/v1/events", json=valid_event(), headers=AUTH)

    assert first.status_code == second.status_code == 200
    assert first.json()["event_id"] == second.json()["event_id"]
    assert first.json()["duplicate"] is False
    assert second.json()["duplicate"] is True
    assert db.count_events("user_001") == 1


def test_different_keys_store_two_events():
    client.post("/v1/events", json=valid_event(idempotency_key="key_1"), headers=AUTH)
    client.post("/v1/events", json=valid_event(idempotency_key="key_2"), headers=AUTH)
    assert db.count_events("user_001") == 2


def test_idempotency_key_expires():
    """abc.md:222 — Redis gives keys a lifetime instead of forever."""
    client.post("/v1/events", json=valid_event(), headers=AUTH)
    key = cache.config.redis_key("idem", "user_001", "key_1")
    ttl = cache.client().ttl(key)
    assert 0 < ttl <= cache.IDEMPOTENCY_TTL_SECONDS


# 5. Storage ----------------------------------------------------------------

def test_stored_event_keeps_its_fields():
    r = client.post("/v1/events", json=valid_event(), headers=AUTH)
    with db.connect() as conn:
        row = conn.execute(
            "SELECT subject_id, event_type, surface, source_event_id, service_id"
            " FROM ingested_event WHERE event_id = %s",
            (r.json()["event_id"],),
        ).fetchone()

    assert row == ("user_001", "playback", "player", "src_1", "chat-surface")


def test_stored_event_has_its_own_expiry():
    """abc.md:109 — raw events expire separately from memories."""
    r = client.post("/v1/events", json=valid_event(), headers=AUTH)
    with db.connect() as conn:
        row = conn.execute(
            "SELECT received_at, expires_at FROM ingested_event WHERE event_id = %s",
            (r.json()["event_id"],),
        ).fetchone()
    assert row[1] > row[0]


def test_event_survives_a_restart():
    """It is in Postgres, not in this process's memory."""
    r = client.post("/v1/events", json=valid_event(), headers=AUTH)
    event_id = r.json()["event_id"]

    # A brand-new connection, as a restarted server would make.
    with db.connect() as conn:
        found = conn.execute(
            "SELECT 1 FROM ingested_event WHERE event_id = %s", (event_id,)
        ).fetchone()
    assert found is not None


# 6. Content — what was said or played --------------------------------------
#
# abc.md:107 accepts "explicit preference statements"; abc.md:134 calls this
# "stored free text" and requires it be treated as untrusted data.

def test_content_is_stored():
    text = "play me something for studying"
    r = client.post("/v1/events", json=valid_event(content=text), headers=AUTH)
    assert r.status_code == 200

    event = db.get_event(r.json()["event_id"], "user_001")
    assert event["content"] == text


def test_content_is_optional():
    """A skip or a follow has no words in it."""
    r = client.post("/v1/events", json=valid_event(), headers=AUTH)
    assert r.status_code == 200
    assert db.get_event(r.json()["event_id"], "user_001")["content"] is None


def test_content_survives_other_languages():
    """abc.md:294 - golden sets include multilingual statements."""
    text = "मुझे country music पसंद नहीं"
    r = client.post("/v1/events", json=valid_event(content=text), headers=AUTH)
    assert db.get_event(r.json()["event_id"], "user_001")["content"] == text


def test_content_never_reaches_the_audit_log():
    """abc.md:322 - logs carry identifiers and outcomes, not private content."""
    secret = "i listen to sad songs when i am lonely"
    client.post("/v1/events", json=valid_event(content=secret), headers=AUTH)
    entry = db.get_audit("user_001")[0]
    assert secret not in str(entry)


def test_an_event_cannot_be_read_by_another_subject():
    """abc.md:110 - subject isolation at the query boundary."""
    r = client.post("/v1/events", json=valid_event(content="private"), headers=AUTH)
    event_id = r.json()["event_id"]

    assert db.get_event(event_id, "user_001") is not None
    assert db.get_event(event_id, "user_002") is None   # knowing the id is not enough
