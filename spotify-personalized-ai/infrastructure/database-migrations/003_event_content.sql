-- Why this migration exists
-- =========================
--
-- The event table recorded THAT something happened, never WHAT. Endpoint 2
-- (POST /v1/memories/extract) has to classify what the listener said, and
-- it cannot classify text that was never stored.
--
-- The specification requires the text without naming a field for it:
--   abc.md:107 - accept "explicit preference statements" as events
--   abc.md:114 - "Deduplicate semantically equivalent statements"
--   abc.md:134 - "Treat stored free text as untrusted data"
--   abc.md:294 - golden sets include "multilingual statements"
--
-- Nullable on purpose: a skip or a follow has no words in it. Existing
-- rows get NULL, and existing callers keep working unchanged.

ALTER TABLE ingested_event
    ADD COLUMN IF NOT EXISTS content TEXT;

COMMENT ON COLUMN ingested_event.content IS
    'What was said or played. Untrusted free text (abc.md:134) - quoted as '
    'data, never placed in a system instruction. Never copied into audit_log.';
