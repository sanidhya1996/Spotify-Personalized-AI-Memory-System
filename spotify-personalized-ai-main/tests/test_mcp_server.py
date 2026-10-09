"""Why this file exists
=====================

abc.md §5.4 "MCP and Tool Interface": exactly five tools, typed, bound to
an authenticated subject, audited, and never a generic graph query tool.
These tests check each of those, against the real stores.
"""

import asyncio

import pytest

from memory import db, embeddings, graph, mcp_server

FIVE_TOOLS = {"search_memory", "add_explicit_preference", "correct_memory",
              "delete_memory", "explain_memory_use"}


@pytest.fixture(autouse=True)
def clean():
    graph.ensure_constraints()
    embeddings.ensure_index()
    for subject in ("user_001", "user_002"):
        graph.delete_memories(subject)
    mcp_server.bind("user_001")
    yield
    for subject in ("user_001", "user_002"):
        graph.delete_memories(subject)


# The model sees exactly the five tools, and none of them is a raw query.
def test_exactly_the_five_required_tools():
    tools = asyncio.run(mcp_server.server.list_tools())
    assert {t.name for t in tools} == FIVE_TOOLS


# No tool lets the model choose whose memory it touches.
def test_no_tool_takes_a_subject_id():
    tools = asyncio.run(mcp_server.server.list_tools())
    for tool in tools:
        assert "subject_id" not in tool.input_schema.get("properties", {})


# Add a preference, then find it again by meaning.
def test_add_then_search():
    added = mcp_server.add_explicit_preference("Loves jazz piano", ["jazz"])
    assert added["memory_id"].startswith("mem_")

    found = mcp_server.search_memory("some jazz please")
    assert [r["memory_id"] for r in found["results"]] == [added["memory_id"]]


# A correction supersedes; the old memory survives as history.
def test_correct_keeps_history():
    old = mcp_server.add_explicit_preference("Loves jazz piano", ["jazz"])["memory_id"]
    result = mcp_server.correct_memory(old, "Loves jazz guitar", ["jazz"])
    assert result["superseded"] == old
    assert graph.get_memory(old, "user_001")["status"] == "superseded"


# Delete returns a job id to follow.
def test_delete_starts_a_job():
    memory_id = mcp_server.add_explicit_preference("Loves jazz piano", ["jazz"])["memory_id"]
    job = mcp_server.delete_memory(memory_id)
    assert job["job_id"] and job["memory_id"] == memory_id


# Explain shows the trace decisions for that one memory.
def test_explain_memory_use():
    memory_id = mcp_server.add_explicit_preference("Loves jazz piano", ["jazz"])["memory_id"]
    trace_id = mcp_server.search_memory("jazz")["trace_id"]
    explained = mcp_server.explain_memory_use(memory_id, trace_id)
    assert explained["decisions"]
    assert all(d["memory_id"] == memory_id for d in explained["decisions"])


# A server bound to user_002 cannot touch user_001's memory.
def test_another_subject_cannot_reach_a_memory():
    memory_id = mcp_server.add_explicit_preference("Loves jazz piano", ["jazz"])["memory_id"]

    mcp_server.bind("user_002")
    assert mcp_server.search_memory("jazz")["results"] == []
    assert "error" in mcp_server.correct_memory(memory_id, "Hates jazz")
    assert "error" in mcp_server.delete_memory(memory_id)
    assert graph.get_memory(memory_id, "user_001")["status"] == "active"


# Every tool call leaves an audit row.
def test_tool_calls_are_audited():
    mcp_server.search_memory("jazz")
    actions = [row["action"] for row in db.get_audit("user_001")]
    assert "mcp.search_memory" in actions


# Errors come back as stable codes, not exceptions.
def test_consent_denied_is_a_stable_error():
    mcp_server.bind("user_004")
    result = mcp_server.search_memory("jazz")
    assert result["error"]["code"] == "CONSENT_DENIED"
