-- Audit trail.
--
-- abc.md:460 - the backend category is scored on "typed contracts,
-- authentication, idempotency, queue integration, error handling, stable
-- APIs, and audit events."
--
-- abc.md:322 - "Logs contain identifiers and outcomes, not raw private
-- content."
--
-- So every column here is an identifier or an outcome. There is no column
-- for what the listener played, said, or searched for - on purpose.

CREATE TABLE IF NOT EXISTS audit_log (
    id              BIGSERIAL PRIMARY KEY,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    correlation_id  TEXT NOT NULL,
    action          TEXT NOT NULL,    -- event.accepted, event.rejected, ...
    subject_id      TEXT NOT NULL,
    service_id      TEXT NOT NULL,
    outcome         TEXT NOT NULL,    -- accepted, duplicate, rejected
    reason          TEXT,             -- the stable error code, when rejected
    event_id        TEXT
);

-- Reads are subject-scoped (abc.md:110), so the subject leads.
CREATE INDEX IF NOT EXISTS audit_log_subject_idx
    ON audit_log (subject_id, recorded_at DESC);

-- Following one request across services.
CREATE INDEX IF NOT EXISTS audit_log_correlation_idx
    ON audit_log (correlation_id);
