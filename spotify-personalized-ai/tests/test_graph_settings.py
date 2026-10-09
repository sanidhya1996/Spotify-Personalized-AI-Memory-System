"""Why this file exists
=====================

On a server there is no .env file - settings are environment variables.
memory/graph.py once read Neo4j's address only from .env, so a deployed app
would quietly have connected to localhost. This checks the environment wins.
"""

from neo4j import GraphDatabase

from memory import graph


def test_neo4j_settings_come_from_the_environment(monkeypatch):
    seen = {}

    def fake_driver(uri, auth):
        seen["uri"], seen["auth"] = uri, auth
        return object()

    monkeypatch.setenv("NEO4J_URI", "neo4j+s://example.databases.neo4j.io")
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "from-the-server")
    monkeypatch.setattr(GraphDatabase, "driver", fake_driver)
    monkeypatch.setattr(graph, "_driver", None)

    graph.driver()

    assert seen == {"uri": "neo4j+s://example.databases.neo4j.io",
                    "auth": ("neo4j", "from-the-server")}
