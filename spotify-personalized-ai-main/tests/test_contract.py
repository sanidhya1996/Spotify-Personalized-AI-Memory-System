"""Contract tests — the event schema must not change by accident.

abc.md:352 — "Backward compatibility for event, API, MCP tool, graph,
vector, and context-package schemas."

Callers integrate against this shape. Renaming or removing a required
field breaks every one of them, silently, until their events start
failing. These tests turn that into a failing build instead.

`data/schemas/event_v1.json` is the frozen copy. If a test here fails,
either the change was a mistake — or it is deliberate, in which case it
needs a NEW schema version, not an edit to this one.
"""

import json
from pathlib import Path

import pytest

from memory.models import SUPPORTED_SCHEMA_VERSION, Event

FROZEN = json.loads(
    (Path(__file__).parent.parent / "data" / "schemas" / "event_v1.json").read_text()
)


def live_schema() -> dict:
    return Event.model_json_schema()


# --- Required fields ------------------------------------------------------

def test_no_required_field_was_removed_or_renamed():
    """The nine required fields of abc.md:233 must all still be there."""
    live = set(live_schema()["required"])
    frozen = set(FROZEN["required"])

    missing = frozen - live
    assert not missing, (
        f"required field(s) removed or renamed: {sorted(missing)}. "
        "This breaks every caller. Add a new schema version instead."
    )


def test_no_new_required_field_was_added():
    """Adding a required field also breaks callers — their events lack it."""
    live = set(live_schema()["required"])
    frozen = set(FROZEN["required"])

    added = live - frozen
    assert not added, (
        f"new required field(s): {sorted(added)}. Existing callers do not "
        "send these, so their events would start failing. Make it optional "
        "or add a new schema version."
    )


@pytest.mark.parametrize("field", FROZEN["required"])
def test_each_required_field_still_exists(field):
    assert field in live_schema()["properties"], f"{field} is gone"


# --- Allowed values -------------------------------------------------------

@pytest.mark.parametrize("field,values", FROZEN["allowed_values"].items())
def test_no_allowed_value_was_removed(field, values):
    """A caller sending a value we used to accept must not start failing."""
    live = set(live_schema()["properties"][field]["enum"])
    removed = set(values) - live
    assert not removed, (
        f"{field} no longer accepts {sorted(removed)}. Callers still send "
        "these values."
    )


# --- Version --------------------------------------------------------------

def test_supported_version_matches_the_frozen_schema():
    assert SUPPORTED_SCHEMA_VERSION == FROZEN["version"]


# --- Response shape -------------------------------------------------------

def test_the_response_still_has_its_three_fields():
    """Callers read these. abc.md:303 — return a stable result."""
    from memory.models import EventAccepted

    properties = EventAccepted.model_json_schema()["properties"]
    for field in ("event_id", "accepted", "duplicate"):
        assert field in properties, f"response field {field} is gone"
