"""In-memory storage.

Plain dicts for now. Swapping these for Redis and a real queue later means
changing this file only — the endpoints do not know the difference.

Every lookup is partitioned by subject. abc.md:296 requires "subject
partition keys", and abc.md:110 requires subject isolation "at the query
boundary" — so the subject is part of the key, not just a stored field.
Authentication guards the door; this guards every lookup behind it.
"""

# event_id -> the stored Event, as a dict
events: dict[str, dict] = {}

# (subject_id, idempotency_key) -> event_id
#
# The subject is half the key on purpose. Idempotency keys are chosen by
# the caller, so two subjects will eventually pick the same one. Keying on
# the key alone would hand one subject another subject's event_id and drop
# their event.
idempotency: dict[tuple[str, str], str] = {}


def reset() -> None:
    """Empty everything. Used by tests so they do not leak into each other."""
    events.clear()
    idempotency.clear()
