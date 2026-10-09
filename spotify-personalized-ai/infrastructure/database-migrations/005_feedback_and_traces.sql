-- Why this migration exists
-- =========================
--
-- abc.md:318 - POST /v1/feedback must "record relevance, correction,
--              rejection, or experience feedback WITHOUT SELF-VALIDATING
--              MODEL OUTPUT."
-- abc.md:149 - "Capture user corrections and reviewer decisions without
--              automatically reinforcing model-generated claims."
-- abc.md:320 - GET /v1/traces/{trace_id} must "return authorized
--              retrieval and policy decisions with sensitive fields
--              redacted."
-- abc.md:101 - the trace "replays the retrieval trace, shows candidate
--              scores and policy decisions, and links the output to
--              source memories."
--
-- Two tables, for two different jobs.
--
-- `feedback` records what a listener thought of a memory. The column that
-- matters most is `reinforced`: it is false for anything the model
-- produced. A system that lets a thumbs-up on its own guess make that
-- guess more certain is marking its own homework, which is exactly what
-- abc.md:149 forbids.
--
-- `trace_decision` records why a memory was included or dropped during
-- one request. Without it, "why did it say that?" has no answer, and
-- abc.md:101 makes answering that a product requirement.

CREATE TABLE IF NOT EXISTS feedback (
    feedback_id     TEXT PRIMARY KEY,
    subject_id      TEXT NOT NULL,
    memory_id       TEXT,
    trace_id        TEXT,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- The four kinds abc.md:318 names.
    kind            TEXT NOT NULL
                    CHECK (kind IN ('relevance', 'correction',
                                    'rejection', 'experience')),

    -- helpful | unhelpful | wrong
    sentiment       TEXT NOT NULL
                    CHECK (sentiment IN ('helpful', 'unhelpful', 'wrong')),

    -- Whether this feedback was allowed to change the memory's standing.
    --
    -- abc.md:149 - false for anything the model produced. Positive
    -- feedback on a model-generated claim is recorded, and deliberately
    -- changes nothing.
    reinforced      BOOLEAN NOT NULL DEFAULT false,

    -- Why it was or was not reinforced, in plain words.
    reinforce_reason TEXT
);

CREATE INDEX IF NOT EXISTS feedback_subject_idx
    ON feedback (subject_id, recorded_at DESC);

CREATE INDEX IF NOT EXISTS feedback_memory_idx
    ON feedback (memory_id);


-- One row per decision taken while answering a request.
CREATE TABLE IF NOT EXISTS trace_decision (
    id              BIGSERIAL PRIMARY KEY,
    trace_id        TEXT NOT NULL,
    subject_id      TEXT NOT NULL,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- retrieval | ranking | policy | composition
    stage           TEXT NOT NULL,

    memory_id       TEXT,

    -- included | excluded
    decision        TEXT NOT NULL,

    -- Why, in a form a person can read.
    reason          TEXT,

    -- The score, where there was one.
    score           DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS trace_decision_trace_idx
    ON trace_decision (trace_id);

-- Reads are subject-scoped (abc.md:119), so the subject leads.
CREATE INDEX IF NOT EXISTS trace_decision_subject_idx
    ON trace_decision (subject_id, recorded_at DESC);
