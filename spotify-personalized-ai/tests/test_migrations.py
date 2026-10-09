"""Why this file exists
=====================

A deployed database is built only from infrastructure/database-migrations.
If a column exists locally because somebody added it by hand, the deployed
app breaks. That happened once: audit_log.memory_id (fixed by migration 009).

This builds a brand-new database from the migrations alone and checks every
column the code writes is there.
"""

import psycopg
import pytest

from memory import config, startup

FRESH = "migrations_check_tmp"

# The columns memory/db.py writes, per table - the ones that must come from a
# migration, not from a hand-edited database.
WRITTEN = {
    "audit_log": {"action", "subject_id", "service_id", "outcome",
                  "correlation_id", "reason", "event_id", "memory_id"},
    "consent": {"subject_id", "state", "updated_at", "region", "age_band"},
    "account": {"subject_id", "password_hash", "created_at"},
}


@pytest.fixture
def fresh_database(monkeypatch):
    url = config.postgres_url()
    fresh = url.rpartition("/")[0] + "/" + FRESH
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {FRESH}")
        conn.execute(f"CREATE DATABASE {FRESH}")
    monkeypatch.setenv("DATABASE_URL", fresh)
    yield fresh
    monkeypatch.delenv("DATABASE_URL")
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {FRESH} WITH (FORCE)")


def test_a_fresh_database_has_every_column_the_code_writes(fresh_database):
    startup.apply_migrations()
    with psycopg.connect(fresh_database) as conn:
        for table, needed in WRITTEN.items():
            have = {row[0] for row in conn.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = %s", (table,)).fetchall()}
            assert needed <= have, (table, needed - have)
