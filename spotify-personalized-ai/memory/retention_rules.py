"""Why this file exists
=====================

Shortens how long a memory is kept, based on where the listener is and how
old they are.

abc.md §5.4 - "Apply retention by memory type, geography, age-related
policy, and consent state."

    by memory type  -> data/policy_registry.yaml   (already existed)
    by consent      -> checked on every request      (already existed)
    by geography    -> this file
    by age          -> this file

The rules themselves are in data/retention_rules.yaml. This file only reads
them and applies the strictest one. A rule can make retention shorter,
never longer.

Where it is used
----------------
memory/policy.py, `classify` - every memory gets its policy class there,
including how many days it is kept. When `classify` is given the subject, it
calls `limit_for_subject` here. That covers every way a memory is written:
POST /v1/memories, the worker (memory/processor.py), extraction and
corrections.

Where the facts come from
-------------------------
The subject's `region` and `age_band` columns on the consent table
(migration 007), set when the user is created through PATCH /v1/consent.
"""

from functools import cache
from pathlib import Path

import yaml

from memory import db

RULES_PATH = Path(__file__).parent.parent / "data" / "retention_rules.yaml"


# Load the rules once; they do not change while the service runs.
@cache
def rules() -> dict:
    return yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))


# The shortest retention allowed for this region and age band.
def limit(retention_days: int, region: str | None, age_band: str) -> tuple[int, list[str]]:
    """Returns (days, reasons). reasons says which rules shortened it, so the
    decision can be explained; it is empty when nothing applied.
    """
    days, reasons = retention_days, []

    strict = rules()["geography"]["strict_regions"]
    if region and region.upper() in strict["countries"] and days > strict["max_retention_days"]:
        days = strict["max_retention_days"]
        reasons.append(f"region {region.upper()}: at most {days} days")

    young = rules()["age"]["under_18"]
    if age_band == "under_18" and days > young["max_retention_days"]:
        days = young["max_retention_days"]
        reasons.append(f"under 18: at most {days} days")

    return days, reasons


# The same, looking up the subject's region and age band first.
def limit_for_subject(retention_days: int, subject_id: str) -> tuple[int, list[str]]:
    region, age_band = db.get_region_and_age(subject_id)
    return limit(retention_days, region, age_band)
