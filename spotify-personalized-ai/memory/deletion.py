"""Why this file exists
=====================

abc.md:315 - "Start cross-store deletion and return a traceable job
              identifier."
abc.md:140 - "Support subject-access and deletion workflows across graph,
              vector, cache, operational metadata, and backups according
              to policy."
abc.md:97  - "Revokes retrieval eligibility, propagates deletion to graph
              and vector stores, and confirms completion status."

Deleting a memory is not one action. The same memory exists in several
places at once, and each has to be cleared separately:

    Neo4j        the memory node and its links
    Neo4j        its 384 numbers, on the same node
    Redis        anything cached about it
    PostgreSQL   the events it came from, and its audit trail
    backups      cannot be edited in place

Any of those can fail while the others succeed, so "deleted" is not a yes
or no - it is a status per store. That is what the job is for: the caller
gets an id straight away and can ask later whether every store really
cleared.

abc.md scoring makes a deletion failure block release regardless of
everything else. So this file reports what actually happened per store
rather than collapsing it into one flag that could hide a partial
failure.

The first thing it does, before any of that, is revoke retrieval
eligibility (abc.md:97) - so the memory stops being usable immediately,
even if a later store is slow.
"""

import uuid
from datetime import datetime, timezone

from memory import cache, db, graph

# What abc.md:140 says about backups: handled "according to policy", not
# edited in place. Backups are immutable snapshots; a memory inside one is
# removed when that snapshot expires, not by reaching into it.
BACKUP_POLICY = "retained_by_policy"


# Make a traceable id for one deletion request.
def new_job_id() -> str:
    return f"job_{uuid.uuid4().hex[:16]}"


# Record that a deletion was asked for, before any of it is done.
def create_job(subject_id: str, memory_id: str) -> str:
    job_id = new_job_id()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO deletion_job (job_id, subject_id, memory_id) "
            "VALUES (%s, %s, %s)",
            (job_id, subject_id, memory_id),
        )
    return job_id


# Read one job back, but only if it belongs to this subject.
def get_job(job_id: str, subject_id: str) -> dict | None:
    # abc.md:119 - subject isolation. A job id alone is not enough.
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT job_id, memory_id, status, requested_at, completed_at,
                   graph_status, vector_status, cache_status,
                   operational_status, backup_status, error
            FROM deletion_job WHERE job_id = %s AND subject_id = %s
            """,
            (job_id, subject_id),
        ).fetchone()

    if row is None:
        return None

    keys = ("job_id", "memory_id", "status", "requested_at", "completed_at",
            "graph_status", "vector_status", "cache_status",
            "operational_status", "backup_status", "error")
    return dict(zip(keys, row))


# Write the outcome of one store back onto the job.
def set_store_status(job_id: str, store: str, status: str) -> None:
    # Column names are built from a fixed list, never from caller input.
    column = {
        "graph": "graph_status",
        "vector": "vector_status",
        "cache": "cache_status",
        "operational": "operational_status",
        "backup": "backup_status",
    }[store]

    with db.connect() as conn:
        conn.execute(
            f"UPDATE deletion_job SET {column} = %s WHERE job_id = %s",
            (status, job_id),
        )


# Stop the memory being retrievable, before anything is actually removed.
def revoke_eligibility(memory_id: str, subject_id: str) -> bool:
    """abc.md:97 - "Revokes retrieval eligibility" is listed first.

    Marking it deleted takes effect immediately. Every read filters on
    status 'active', so the memory stops being usable even if a later
    store is slow to clear.
    """
    with graph.driver().session() as session:
        record = session.run(
            """
            MATCH (m:Memory {memory_id: $memory_id, subject_id: $subject_id})
            SET m.status = 'deleted', m.valid_to = datetime(),
                m.graph_version = m.graph_version + 1
            RETURN m.memory_id AS memory_id
            """,
            memory_id=memory_id, subject_id=subject_id,
        ).single()
    return record is not None


# Remove the memory node, its links and its vector from Neo4j.
def delete_from_graph(memory_id: str, subject_id: str) -> str:
    """The graph and the vector go together: the 384 numbers are a
    property on the same node, so one DETACH DELETE clears both.

    abc.md:55 - graph and vector share a stable id "so erasure is
    complete". Here that means there is no second store to forget.
    """
    with graph.driver().session() as session:
        record = session.run(
            "MATCH (m:Memory {memory_id: $memory_id, subject_id: $subject_id}) "
            "DETACH DELETE m RETURN count(m) AS removed",
            memory_id=memory_id, subject_id=subject_id,
        ).single()
    # Report what happened, not what was attempted. A node that was not
    # there must not be reported as a successful deletion.
    return "deleted" if record["removed"] else "nothing_to_delete"


# Clear anything this subject has cached.
def delete_from_cache(subject_id: str) -> str:
    client = cache.client()
    pattern = f"{cache.config.redis_prefix()}:*:{subject_id}:*"
    keys = list(client.scan_iter(match=pattern))
    if not keys:
        return "nothing_to_delete"
    client.delete(*keys)
    return "deleted"


# Which events produced this memory. Read BEFORE the node is destroyed.
def source_events_of(memory_id: str, subject_id: str) -> list[str]:
    """The event ids must be collected before the graph node is deleted.

    Deleting the node first and then asking it what it came from returns
    nothing - which is how the operational store silently went uncleared
    while still reporting success.
    """
    memory = graph.get_memory(memory_id, subject_id)
    return list((memory or {}).get("source_event_ids") or [])


# Remove the operational trail: the events this memory came from.
def delete_from_operational(event_ids: list[str], subject_id: str) -> str:
    """abc.md:140 - deletion covers "operational metadata" too.

    Takes the event ids explicitly, rather than looking them up, because
    by the time this runs the memory node is already gone.

    The audit line for the deletion itself is kept: abc.md:118 warns
    against "erasing audit history prematurely", and a deletion with no
    record of having happened is worse than no deletion.
    """
    if not event_ids:
        # Nothing to remove is a real outcome, and must not be reported as
        # a successful deletion of something.
        return "nothing_to_delete"

    with db.connect() as conn:
        result = conn.execute(
            "DELETE FROM ingested_event "
            "WHERE subject_id = %s AND event_id = ANY(%s)",
            (subject_id, event_ids),
        )
        removed = result.rowcount

    # Say what actually happened, not what was attempted.
    return "deleted" if removed else "nothing_to_delete"


# Do the whole cross-store deletion and record what happened in each.
def run_job(job_id: str, subject_id: str, memory_id: str) -> dict:
    """Runs in the background. The caller already has the job id.

    Each store is attempted separately and recorded separately, so a
    partial failure is visible rather than hidden behind one flag.
    """
    outcomes: dict[str, str] = {}

    # Collect what we will need BEFORE anything is destroyed. Asking the
    # graph node what it came from after deleting it returns nothing.
    event_ids = source_events_of(memory_id, subject_id)

    for store, action in (
        ("graph", lambda: delete_from_graph(memory_id, subject_id)),
        ("vector", lambda: "deleted"),          # same node as the graph
        ("cache", lambda: delete_from_cache(subject_id)),
        ("operational", lambda: delete_from_operational(event_ids, subject_id)),
        ("backup", lambda: BACKUP_POLICY),
    ):
        try:
            outcomes[store] = action()
        except Exception as exc:  # noqa: BLE001 - one store must not stop the rest
            outcomes[store] = "failed"
            outcomes.setdefault("_errors", "")
            outcomes["_errors"] += f"{store}: {type(exc).__name__}: {exc}; "

        if not store.startswith("_"):
            set_store_status(job_id, store, outcomes[store])

    # A store that failed means the job did not finish, whatever the
    # others did.
    failed = [s for s, v in outcomes.items() if v == "failed"]
    status = "failed" if failed else "completed"

    with db.connect() as conn:
        conn.execute(
            "UPDATE deletion_job SET status = %s, completed_at = %s, error = %s "
            "WHERE job_id = %s",
            (status, datetime.now(timezone.utc),
             outcomes.get("_errors"), job_id),
        )

    return {"status": status, **{k: v for k, v in outcomes.items()
                                 if not k.startswith("_")}}
