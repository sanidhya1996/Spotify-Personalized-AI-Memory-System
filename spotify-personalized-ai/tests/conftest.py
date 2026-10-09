"""Shared test setup.

These tests run against the real Postgres and Redis from .env, because
that is what the endpoint now uses. Each test starts from a clean slate
for the subjects it touches, so tests cannot affect each other.
"""

import pytest

from memory import cache, db

# Every subject the tests use.
TEST_SUBJECTS = [f"user_{n:03d}" for n in range(1, 6)] + ["user_999"]

# How the seeded consent table should look at the start of each test.
DEFAULT_CONSENT = {
    "user_001": "granted",
    "user_002": "granted",
    "user_003": "granted",
    "user_004": "denied",
    "user_005": "paused",
}


def clean_subject(subject_id: str) -> None:
    """Remove a subject's events, audit lines, and Redis keys."""
    db.delete_events(subject_id)
    db.delete_audit(subject_id)
    # NOT `with cache.client() as ...` - that closes the shared connection
    # when the block ends, and every later test then works with a closed
    # client. The cleanup stops running, the rate-limit counter survives,
    # and tests fail depending only on what ran before them.
    client = cache.client()
    pattern = f"{cache.config.redis_prefix()}:*:{subject_id}:*"
    keys = list(client.scan_iter(match=pattern))
    if keys:
        client.delete(*keys)


@pytest.fixture(autouse=True)
def clean_stores():
    """Reset every test subject before and after each test."""
    def reset():
        for subject_id in TEST_SUBJECTS:
            clean_subject(subject_id)
        for subject_id, state in DEFAULT_CONSENT.items():
            db.set_consent(subject_id, state)

    reset()
    yield
    reset()
