"""Why this file exists
=====================

Explains each of the five memory types in plain words: what it is, an
example, and a counterexample.

abc.md §7.2 step 1 - "For each type, document definition, example,
counterexample, sensitivity, retention, and retrieval eligibility."

The six fields live in two files, on purpose:

    definition, example, counterexample     -> data/memory_types.yaml
                                               (read by people)
    sensitivity, retention, eligibility     -> data/policy_registry.yaml
                                               (enforced by memory/policy.py)

This file loads the first and refuses to start if any type is missing a
field, so the documentation cannot quietly fall behind the code.

Where it is used
----------------
memory/api.py, GET /policy - returned as "definitions" beside the policy
registry. The console's "Schema & policy" screen shows them under each type.
"""

from functools import cache
from pathlib import Path

import yaml

from memory.models import MEMORY_TYPES

TYPES_PATH = Path(__file__).parent.parent / "data" / "memory_types.yaml"
FIELDS = ("definition", "example", "counterexample")


# Load the definitions once, checking every type has all three fields.
@cache
def definitions() -> dict:
    entries = yaml.safe_load(TYPES_PATH.read_text(encoding="utf-8"))

    missing = set(MEMORY_TYPES) - set(entries)
    if missing:
        raise ValueError(f"memory_types.yaml is missing: {sorted(missing)}")

    for name in MEMORY_TYPES:
        for field in FIELDS:
            if not str(entries[name].get(field, "")).strip():
                raise ValueError(f"memory_types.yaml: {name} has no {field}")

    return {name: {field: entries[name][field] for field in FIELDS}
            for name in MEMORY_TYPES}
