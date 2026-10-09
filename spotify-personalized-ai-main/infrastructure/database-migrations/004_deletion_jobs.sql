-- Why this migration exists
-- =========================
--
-- abc.md:315 - DELETE /v1/memories/{id} must "start cross-store deletion
--              and return a traceable job identifier."
-- abc.md:317 - GET /v1/deletions/{job_id} must "report graph, vector,
--              cache, operational-store, and backup-policy status."
-- abc.md:140 - "Support subject-access and deletion workflows across
--              graph, vector, cache, operational metadata, and backups
--              according to policy."
--
-- Deletion is not one action. A memory lives in several places at once,
-- and each has to be cleared separately. Some may fail while others
-- succeed, so "deleted" is not a yes or no - it is a status per store.
--
-- That is why a job exists at all: the caller gets an id immediately and
-- can ask later whether every store actually cleared.
--
-- abc.md scoring makes a deletion failure block release regardless of
-- anything else, which is why each store is recorded separately rather
-- than collapsed into one flag that could hide a partial failure.

CREATE TABLE IF NOT EXISTS deletion_job (
    job_id          TEXT PRIMARY KEY,
    subject_id      TEXT NOT NULL,
    memory_id       TEXT NOT NULL,
    requested_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ,

    -- pending | completed | partial | failed
    status          TEXT NOT NULL DEFAULT 'pending',

    -- One column per store abc.md:317 names. Each is
    -- pending | deleted | failed | retained_by_policy
    graph_status        TEXT NOT NULL DEFAULT 'pending',
    vector_status       TEXT NOT NULL DEFAULT 'pending',
    cache_status        TEXT NOT NULL DEFAULT 'pending',
    operational_status  TEXT NOT NULL DEFAULT 'pending',

    -- Backups cannot be edited in place. abc.md:140 says "according to
    -- policy", so this records what the policy is rather than pretending
    -- the data is gone.
    backup_status       TEXT NOT NULL DEFAULT 'pending',

    error           TEXT
);

-- Reads are subject-scoped (abc.md:119), so the subject leads.
CREATE INDEX IF NOT EXISTS deletion_job_subject_idx
    ON deletion_job (subject_id, requested_at DESC);

CREATE INDEX IF NOT EXISTS deletion_job_memory_idx
    ON deletion_job (memory_id);
