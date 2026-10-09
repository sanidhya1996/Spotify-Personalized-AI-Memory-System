"""Why this file exists
=====================

abc.md §7.2 step 1 - for each memory type, "document definition, example,
counterexample, sensitivity, retention, and retrieval eligibility."

Checks all six fields exist for all five types - three in
data/memory_types.yaml, three in data/policy_registry.yaml - and that
GET /policy returns them for the "Schema & policy" screen.
"""

from fastapi.testclient import TestClient

from memory import memory_types, policy
from memory.api import app
from memory.auth import mint_token
from memory.models import MEMORY_TYPES

client = TestClient(app)
AUTH = {"Authorization": f"Bearer {mint_token('user_001', 'memory-console')}"}


def test_every_type_has_all_six_fields():
    definitions = memory_types.definitions()
    registry = policy.registry()
    for name in MEMORY_TYPES:
        for field in ("definition", "example", "counterexample"):
            assert definitions[name][field].strip(), (name, field)
        for field in ("sensitivity", "retention_days", "retrieval_eligibility"):
            assert field in registry[name], (name, field)


def test_policy_returns_definitions_and_retention_rules():
    body = client.get("/policy", headers=AUTH).json()
    assert set(body["definitions"]) == set(MEMORY_TYPES)
    assert body["retention_rules"]["age"]["under_18"]["max_retention_days"] == 30
