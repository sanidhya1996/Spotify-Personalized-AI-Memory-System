-- Operational store schema.
--
-- abc.md:219 - PostgreSQL "stores consent state, ingestion status, tool
-- audit, experiments, feedback, and deletion jobs."
--
-- This migration covers what POST /v1/events needs: consent state and
-- ingestion status. The rest arrive with their own endpoints.
--
-- Runs automatically the first time the postgres container starts.


-- Consent is OUR record, not the caller's claim.
--
-- abc.md:187 says the ingestion API "verifies ... consent state". The
-- event carries what the surface believes (abc.md:108), and we check it
-- against this table. If they disagree, this table wins.
CREATE TABLE IF NOT EXISTS consent (
    subject_id    TEXT PRIMARY KEY,
    state         TEXT NOT NULL CHECK (state IN ('granted', 'denied', 'paused')),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- Ingestion status: one row per accepted event.
--
-- The raw event lives on its own retention clock, separate from memory
-- retention (abc.md:109 - "not every event becomes a retrievable memory").
CREATE TABLE IF NOT EXISTS ingested_event (
    event_id         TEXT PRIMARY KEY,
    subject_id       TEXT NOT NULL,
    service_id       TEXT NOT NULL,
    event_type       TEXT NOT NULL,
    surface          TEXT NOT NULL,
    locale           TEXT NOT NULL,
    schema_version   TEXT NOT NULL,
    source_event_id  TEXT NOT NULL,
    idempotency_key  TEXT NOT NULL,
    occurred_at      TIMESTAMPTZ NOT NULL,
    received_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at       TIMESTAMPTZ NOT NULL,
    status           TEXT NOT NULL DEFAULT 'accepted'
);

-- Every read is subject-scoped (abc.md:110, "subject isolation at the
-- query boundary"), so the subject leads the index.
CREATE INDEX IF NOT EXISTS ingested_event_subject_idx
    ON ingested_event (subject_id, received_at DESC);

CREATE INDEX IF NOT EXISTS ingested_event_expiry_idx
    ON ingested_event (expires_at);


-- Demo subjects, so the pilot has consent records to verify against.
-- abc.md:245 - seeded data must be synthetic and contain no real history.
INSERT INTO consent (subject_id, state) VALUES
    ('user_001', 'granted'),
    ('user_002', 'granted'),
    ('user_003', 'granted'),
    ('user_004', 'denied'),
    ('user_005', 'paused')
ON CONFLICT (subject_id) DO NOTHING;
