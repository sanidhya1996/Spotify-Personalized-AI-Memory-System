-- Why this migration exists
-- =========================
--
-- The Overview screen has to show seven things (abc.md:339): service
-- health, ingestion lag, retrieval SLO, fallback rate, quality metrics,
-- experiment status, and deletion backlog.
--
-- Three of them already had somewhere to come from. Four did not, and
-- that is what these tables are for.
--
-- abc.md:143 - "Monitor ingestion lag, write failures, retrieval latency,
--              cache effectiveness, fallback rate, deletion backlog, and
--              policy rejection rate."
-- abc.md:170 - "P95 retrieval and context composition should remain
--              within a 250 ms service budget for the pilot."
-- abc.md:146 - "Support memory-enabled and memory-disabled experiments
--              under consistent cohort allocation and guardrails."
-- abc.md:219 - PostgreSQL stores "consent state, ingestion status, tool
--              audit, EXPERIMENTS, feedback, and deletion jobs."
-- abc.md:239 - "Golden evaluation cases with expected memory extraction,
--              graph state, retrieved candidates, policy outcome, and
--              context package."
--
-- Nothing here stores memory text or anything a listener said. These are
-- counts, durations and identifiers - abc.md:144 requires logs to carry
-- "identifiers and outcomes, not raw private content", and the same rule
-- applies to the tables the dashboards read.


-- Retrieval latency, one row per request.
--
-- abc.md:170 sets a P95 budget, and a percentile cannot be derived from a
-- running average - it needs the individual durations. So each request
-- records how long it took, and the metrics endpoint computes P50, P95
-- and P99 from the rows.
--
-- Rows are small and expire quickly: abc.md:170 is about the pilot's
-- current behaviour, not history, so a day is enough.
CREATE TABLE IF NOT EXISTS request_latency (
    id            BIGSERIAL PRIMARY KEY,

    -- Which endpoint, so the retrieval path can be reported separately
    -- from the write path. abc.md:170 budgets retrieval and composition.
    route         TEXT NOT NULL,

    duration_ms   DOUBLE PRECISION NOT NULL,
    status_code   INTEGER NOT NULL,
    recorded_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The percentile query filters by route and time, so both lead the index.
CREATE INDEX IF NOT EXISTS request_latency_route_time_idx
    ON request_latency (route, recorded_at DESC);


-- Fallback events: a request that answered without memory, and why.
--
-- abc.md:135 - "Provide a deterministic no-memory fallback."
-- abc.md:143 - the fallback RATE must be monitored, which means counting
--              the requests that fell back against the ones that did not.
-- abc.md:167 - "Memory retrieval must fail open to a non-personalized
--              response", so a fallback is normal behaviour rather than
--              an error, and has to be counted separately from failures.
CREATE TABLE IF NOT EXISTS fallback_event (
    id            BIGSERIAL PRIMARY KEY,
    subject_id    TEXT NOT NULL,

    -- True when the request answered with no memory at all.
    fell_back     BOOLEAN NOT NULL,

    -- consent paused, nothing relevant found, dependency unhealthy, and
    -- so on. The reason is what makes the rate actionable: a high rate
    -- caused by paused consent is not the same problem as one caused by
    -- an unhealthy graph.
    reason        TEXT,

    correlation_id TEXT,
    recorded_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS fallback_event_time_idx
    ON fallback_event (recorded_at DESC);


-- Experiment cohorts.
--
-- abc.md:146 - memory-enabled and memory-disabled experiments "under
--              consistent cohort allocation". Consistent means a subject
--              keeps the same cohort every time, which is why this is a
--              stored assignment and not a coin flip per request.
-- abc.md:344 - the quality screen needs "side-by-side memory-enabled
--              comparisons", which is only possible if some subjects are
--              deliberately answered without memory.
CREATE TABLE IF NOT EXISTS experiment_cohort (
    subject_id    TEXT PRIMARY KEY,

    -- memory_enabled | memory_disabled
    cohort        TEXT NOT NULL
                  CHECK (cohort IN ('memory_enabled', 'memory_disabled')),

    -- Which experiment this allocation belongs to, so a later experiment
    -- can reallocate without losing what the first one measured.
    experiment    TEXT NOT NULL DEFAULT 'pilot_memory_uplift',

    assigned_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS experiment_cohort_experiment_idx
    ON experiment_cohort (experiment, cohort);


-- Golden-set runs.
--
-- abc.md:148 - "Maintain golden sets for explicit preference, temporal
--              change, contradiction, multilingual interaction, sparse
--              history, and malicious stored text."
-- abc.md:295 - every golden case specifies "the expected graph state,
--              retrieved top memories, prohibited memories, context
--              budget, and correction or deletion outcome."
-- abc.md:344 - the quality screen shows runs, failure clusters,
--              multilingual cases and contradiction cases.
-- abc.md:361 - release is blocked if "provenance falls below threshold",
--              so a run's score is a gate and has to be recorded rather
--              than printed and lost.
--
-- Two tables: one row per run, one row per case within it, so a failure
-- can be traced to the case that produced it.
CREATE TABLE IF NOT EXISTS golden_run (
    run_id          TEXT PRIMARY KEY,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,

    -- running | completed | failed
    status          TEXT NOT NULL DEFAULT 'running',

    total_cases     INTEGER NOT NULL DEFAULT 0,
    passed          INTEGER NOT NULL DEFAULT 0,
    failed          INTEGER NOT NULL DEFAULT 0,

    -- abc.md:82 - "We need precision at the top of the retrieved set, not
    -- just retrieval recall. One wrong memory can be more damaging than
    -- three missing ones." So precision is recorded, not just a pass rate.
    precision_at_k  DOUBLE PRECISION,

    -- abc.md:153 - contradiction rate is one of the offline measures.
    contradiction_rate DOUBLE PRECISION,

    -- abc.md:361 - provenance completeness gates release: every context
    -- item must carry its source.
    provenance_completeness DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS golden_run_time_idx
    ON golden_run (started_at DESC);


CREATE TABLE IF NOT EXISTS golden_case_result (
    id            BIGSERIAL PRIMARY KEY,
    run_id        TEXT NOT NULL REFERENCES golden_run (run_id) ON DELETE CASCADE,

    case_id       TEXT NOT NULL,

    -- abc.md:148 names the categories. Storing it per case is what lets
    -- the screen group failures into clusters (abc.md:344).
    category      TEXT NOT NULL,

    locale        TEXT,
    passed        BOOLEAN NOT NULL,

    -- Which expectation broke, in words. Identifiers and outcomes only -
    -- never the memory text a case expected.
    failure_reason TEXT,

    -- The measures for this one case, so a run's totals can be checked.
    expected_top   INTEGER NOT NULL DEFAULT 0,
    matched_top    INTEGER NOT NULL DEFAULT 0,
    prohibited_leaked INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS golden_case_run_idx
    ON golden_case_result (run_id);

CREATE INDEX IF NOT EXISTS golden_case_category_idx
    ON golden_case_result (category, passed);


-- Seed the cohorts for the demo subjects.
--
-- abc.md:146 wants a memory-disabled arm to compare against, so one of
-- the granted subjects is deliberately allocated to it. user_003 is the
-- spare, which makes it the right one to hold the baseline.
INSERT INTO experiment_cohort (subject_id, cohort) VALUES
    ('user_001', 'memory_enabled'),
    ('user_002', 'memory_enabled'),
    ('user_003', 'memory_disabled'),
    ('user_004', 'memory_enabled'),
    ('user_005', 'memory_enabled')
ON CONFLICT (subject_id) DO NOTHING;
