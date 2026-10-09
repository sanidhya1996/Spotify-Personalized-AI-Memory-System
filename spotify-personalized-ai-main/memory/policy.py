"""Why this file exists
=====================

abc.md:115 - "Assign confidence and policy class using deterministic rules
plus structured model output."

Every memory needs a policy class: how sensitive it is, how long it lives,
and where it may be shown. Later endpoints depend on it - search has to
skip memories not allowed on a surface, composition has to drop expired
ones, and deletion has to know what to remove.

The values themselves live in data/policy_registry.yaml, not here, so a
policy change is a reviewable data change rather than a code edit
(abc.md:237 calls it a "policy registry").

This file only reads that registry and applies it.
"""

from datetime import datetime, timedelta, timezone
from functools import cache
from pathlib import Path

import yaml

from memory import retention_rules
from memory.models import MEMORY_TYPES, PolicyClass

REGISTRY_PATH = Path(__file__).parent.parent / "data" / "policy_registry.yaml"

# The three surfaces abc.md:108 allows.
SURFACES = ("chat", "player", "search")


@cache
def registry() -> dict:
    """Load the registry once and check it covers every memory type.

    Cached because it is read on every extraction and never changes while
    the service is running.
    """
    entries = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))

    missing = set(MEMORY_TYPES) - set(entries)
    if missing:
        raise ValueError(f"policy registry is missing: {sorted(missing)}")

    for name, entry in entries.items():
        for field in ("sensitivity", "retention_days", "retrieval_eligibility"):
            if field not in entry:
                raise ValueError(f"policy registry: {name} has no {field}")

        bad = set(entry["retrieval_eligibility"]) - set(SURFACES)
        if bad:
            raise ValueError(f"policy registry: {name} allows unknown surface {bad}")

    return entries


def classify(memory_type: str, now: datetime | None = None,
             subject_id: str | None = None) -> PolicyClass:
    """Give one memory its policy class, from its type.

    abc.md:139 - "Apply retention BY MEMORY TYPE". The class comes from
    what kind of memory it is, never from a judgement about the individual
    memory, so the same kind is always treated the same way.

    When the subject is given, geography and age can shorten retention
    further (abc.md §5.4) - see memory/retention_rules.py.
    """
    entry = registry()[memory_type]
    now = now or datetime.now(timezone.utc)

    days = entry["retention_days"]
    if subject_id:
        days, _ = retention_rules.limit_for_subject(days, subject_id)

    return PolicyClass(
        sensitivity=entry["sensitivity"],
        retention_days=days,
        retrieval_eligibility=list(entry["retrieval_eligibility"]),
        expires_at=now + timedelta(days=days),
    )


def may_surface(memory_type: str, surface: str) -> bool:
    """Is this kind of memory allowed to be shown on this surface?

    Used by retrieval and context composition (abc.md:133 - exclude
    memories "outside the active surface policy").
    """
    return surface in registry()[memory_type]["retrieval_eligibility"]
