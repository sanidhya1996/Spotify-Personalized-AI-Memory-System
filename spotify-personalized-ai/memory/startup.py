"""Why this file exists
=====================

Gets the stores ready when the API starts, so a deployment needs no manual
step: the tables, the Neo4j constraints and the vector index are created
the first time, and left alone after that.

Locally this is what `python scripts/setup.py` does by hand. On a host like
Render's free plan there is no pre-deploy command and no shell, and a fresh
cloud database (Neon) starts empty - every request that touched it failed
with a 500 until something created the tables. Now the API does it itself.

Every step is safe to repeat: each migration checks before it changes
anything (CREATE ... IF NOT EXISTS, ON CONFLICT DO NOTHING), and so do the
Neo4j constraints and index.

If a store is not reachable yet, the error is logged and the API still
starts - /health keeps answering, and the next restart tries again.

It also starts the worker inside the API when RUN_WORKER_IN_API is set -
for a free host with one web service and no separate background worker
(memory/worker.py).

Where it is used
----------------
memory/api.py - run once when the API starts (the FastAPI lifespan).
scripts/setup.py does the same steps, with progress printed, for local use.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

MIGRATIONS = Path(__file__).parent.parent / "infrastructure" / "database-migrations"


# Apply every migration, in name order. Each one is safe to run again.
def apply_migrations() -> list[str]:
    import psycopg

    from memory import config

    applied = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        with psycopg.connect(config.postgres_url(), autocommit=True) as conn:
            conn.execute(path.read_text(encoding="utf-8"))
        applied.append(path.name)
    return applied


# Create the graph constraints and the vector index if they are missing.
def prepare_graph() -> None:
    from memory import embeddings, graph

    graph.ensure_constraints()
    embeddings.ensure_index()


# Both, logging instead of crashing, so a store that is not up yet cannot
# stop the API from starting.
def prepare_stores() -> None:
    try:
        print("startup: applied " + ", ".join(apply_migrations()), flush=True)
    except Exception:  # noqa: BLE001
        logger.exception("startup: PostgreSQL migrations failed")
    try:
        prepare_graph()
        print("startup: Neo4j constraints and vector index ready", flush=True)
    except Exception:  # noqa: BLE001
        logger.exception("startup: Neo4j preparation failed")

    # On a free host the worker has no service of its own, so it runs here.
    from memory import worker

    # Always say which way it went, so the host's logs answer "is the worker
    # running?" without guessing.
    if worker.runs_in_api():
        worker.start_in_background()
    else:
        print("startup: worker NOT started inside the API - RUN_WORKER_IN_API is "
              f"{os.environ.get('RUN_WORKER_IN_API')!r}; set it to true on a host "
              "without a separate worker", flush=True)
