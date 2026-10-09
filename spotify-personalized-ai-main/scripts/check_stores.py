"""Check that Postgres and Redis are reachable with the settings in .env.

    python scripts/check_stores.py

Prints OK or the exact error for each. Passwords are masked.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory import config  # noqa: E402


def check_postgres() -> bool:
    url = config.postgres_url()
    print(f"postgres : {config.safe(url)}")
    try:
        import psycopg
    except ImportError:
        print("           SKIPPED - run: pip install 'psycopg[binary]'")
        return False

    try:
        with psycopg.connect(url, connect_timeout=10) as conn:
            version = conn.execute("SELECT version()").fetchone()[0]
            tables = conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                " ORDER BY tablename"
            ).fetchall()
    except Exception as exc:  # noqa: BLE001 - we want to show any failure
        print(f"           FAILED - {type(exc).__name__}: {exc}")
        return False

    print(f"           OK - {version.split(',')[0]}")
    names = [t[0] for t in tables]
    if names:
        print(f"           tables: {', '.join(names)}")
    else:
        print("           tables: none yet - run the migration")
    return True


def check_redis() -> bool:
    url = config.redis_url()
    print(f"redis    : {config.safe(url)}")
    try:
        import redis
    except ImportError:
        print("           SKIPPED - run: pip install redis")
        return False

    try:
        client = redis.from_url(url, socket_connect_timeout=10)
        client.ping()
        info = client.info("server")
    except Exception as exc:  # noqa: BLE001
        print(f"           FAILED - {type(exc).__name__}: {exc}")
        return False

    print(f"           OK - redis {info.get('redis_version')}")
    return True


if __name__ == "__main__":
    ok = check_postgres()
    print()
    ok = check_redis() and ok
    print()
    print("all reachable" if ok else "something is not reachable - see above")
    raise SystemExit(0 if ok else 1)
