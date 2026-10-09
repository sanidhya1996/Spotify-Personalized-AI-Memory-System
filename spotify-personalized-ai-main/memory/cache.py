"""Talking to Redis.

Redis is a key-value store where keys can expire by themselves. That is
exactly what idempotency needs: remember a key for a day, then forget it.

abc.md:222 - Redis gives "short-lived subject-scoped caching, rate
limiting, and idempotency support."

Keys look like:  spotifymem:idem:user_001:key_1
                 ^prefix    ^what  ^subject ^the caller's key

The prefix keeps this app separate from anything else using the same
Redis. The subject is in the key because abc.md:296 requires "subject
partition keys" - without it, two subjects choosing the same idempotency
key would collide.
"""

import redis

from memory import config

# How long a used idempotency key is remembered. Long enough to cover a
# retry, short enough that memory does not grow forever.
IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60  # 24 hours

_client: redis.Redis | None = None


def client() -> redis.Redis:
    """One shared connection, made the first time it is needed."""
    global _client
    if _client is None:
        _client = redis.from_url(config.redis_url(), decode_responses=True)
    return _client


def _key(subject_id: str, idempotency_key: str) -> str:
    return config.redis_key("idem", subject_id, idempotency_key)


def get_event_id(subject_id: str, idempotency_key: str) -> str | None:
    """Have we seen this key from this subject before?

    Returns the event_id we gave out last time, or None.
    """
    return client().get(_key(subject_id, idempotency_key))


def remember(subject_id: str, idempotency_key: str, event_id: str) -> None:
    """Record that this key produced this event_id, for 24 hours."""
    client().set(
        _key(subject_id, idempotency_key),
        event_id,
        ex=IDEMPOTENCY_TTL_SECONDS,
    )


def forget(subject_id: str, idempotency_key: str) -> None:
    """Drop one key. Used by tests to clean up."""
    client().delete(_key(subject_id, idempotency_key))


def forget_subject(subject_id: str) -> int:
    """Drop everything cached for one subject, and say how many keys went.

    abc.md:141 - "deletion propagation, cache invalidation" are part of
    honouring a change. A listener who pauses or opts out must not then be
    answered from a cache that was warmed while consent was granted.

    SCAN rather than KEYS, because KEYS blocks the whole server while it
    walks the keyspace and this runs on a live request path.
    """
    pattern = config.redis_key("*", subject_id, "*")
    conn = client()
    removed = 0
    for key in conn.scan_iter(match=pattern, count=100):
        removed += conn.delete(key)
    return removed


# --- Rate limiting --------------------------------------------------------
#
# abc.md:356 lists rate limits as a security test area, and abc.md:128
# requires them on every tool. One caller must not be able to flood us.

RATE_LIMIT_PER_MINUTE = 120


def is_rate_limited(subject_id: str) -> bool:
    """Count this call. True when the subject has sent too many this minute.

    How it works: one Redis counter per subject per minute. The first call
    of a new minute creates the counter and gives it a 60-second life, so
    it cleans itself up. No sliding window, no background job.
    """
    from datetime import datetime, timezone

    minute = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    key = config.redis_key("rate", subject_id, minute)

    count = client().incr(key)
    if count == 1:
        client().expire(key, 60)
    return count > RATE_LIMIT_PER_MINUTE
