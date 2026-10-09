"""Why this file exists
=====================

One command that prepares everything, so a fresh clone can run the project
without knowing which migration goes where.

    python scripts/setup.py

It waits for the stores to come up, applies the PostgreSQL migrations,
creates the Neo4j constraints and the vector index, and seeds the demo
consent records. Safe to run again at any time - every step is written to
be repeatable.
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# Wait until a check passes, or give up and say so.
def wait_for(name: str, check, attempts: int = 30, gap: float = 2.0) -> bool:
    for attempt in range(1, attempts + 1):
        try:
            check()
            print(f"  {name}: ready")
            return True
        except Exception as exc:  # noqa: BLE001 - any failure means not ready
            if attempt == attempts:
                print(f"  {name}: FAILED - {type(exc).__name__}: {exc}")
                return False
            if attempt == 1:
                print(f"  {name}: waiting...", end="", flush=True)
            else:
                print(".", end="", flush=True)
            time.sleep(gap)
    return False


# Apply every .sql migration, in name order.
def apply_postgres_migrations() -> None:
    # The same steps the API runs when it starts (memory/startup.py).
    from memory import startup

    for name in startup.apply_migrations():
        print(f"  applied {name}")


# Create the graph constraints and the vector index.
def prepare_neo4j() -> None:
    from memory import embeddings, graph

    graph.ensure_constraints()
    print("  constraints and indexes ready")
    embeddings.ensure_index()
    print("  vector index ready (the model downloads on first use)")


def main() -> int:
    print("\nChecking the stores are up")
    print("-" * 50)

    from memory import config

    def postgres_check():
        import psycopg
        with psycopg.connect(config.postgres_url(), connect_timeout=3) as conn:
            conn.execute("SELECT 1")

    def redis_check():
        import redis
        redis.from_url(config.redis_url(), socket_connect_timeout=3).ping()

    def neo4j_check():
        from memory import graph
        graph.driver().verify_connectivity()

    ok = True
    ok &= wait_for("postgres", postgres_check)
    ok &= wait_for("redis", redis_check)
    ok &= wait_for("neo4j", neo4j_check)

    if not ok:
        print("\nSomething is not up. Try:  docker compose up -d")
        return 1

    print("\nApplying PostgreSQL migrations")
    print("-" * 50)
    apply_postgres_migrations()

    # The five test users get a login only when DEMO_PASSWORD is set in the
    # private .env (memory/accounts.py). Existing accounts are left alone.
    from memory import accounts
    made = accounts.ensure_demo_accounts()
    print(f"  test-user logins created: {made}"
          + ("" if made or os.environ.get("DEMO_PASSWORD") else " (DEMO_PASSWORD not set)"))

    print("\nPreparing Neo4j")
    print("-" * 50)
    prepare_neo4j()

    print("\n" + "-" * 50)
    print("Ready. Two terminals:")
    print("  1  python -m uvicorn memory.api:app --reload --port 8000")
    print("  2  python scripts/run_processor.py --forever")
    print("\nThen open http://127.0.0.1:8000/docs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
