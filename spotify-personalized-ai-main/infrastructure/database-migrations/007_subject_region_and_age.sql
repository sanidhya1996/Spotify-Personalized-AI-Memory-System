-- Why this file exists
-- ====================
--
-- abc.md §5.4 - "Apply retention by memory type, geography, age-related
-- policy, and consent state." Type and consent were already handled; this
-- adds the two facts geography and age rules need, on the subject's record.
--
-- Kept minimal on purpose (abc.md:53, purpose limitation and minimization):
--   region   - a two-letter country code such as 'DE' or 'IN', or NULL if
--              unknown. Not an address.
--   age_band - 'adult' or 'under_18'. Not a birth date.
--
-- Used by memory/retention_rules.py, which reads these to shorten how long
-- a memory is kept. Set when a user is created (PATCH /v1/consent).
--
-- Safe to run again: every statement checks before it changes anything.

ALTER TABLE consent ADD COLUMN IF NOT EXISTS region TEXT;
ALTER TABLE consent ADD COLUMN IF NOT EXISTS age_band TEXT NOT NULL DEFAULT 'adult';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'consent_age_band_check'
    ) THEN
        ALTER TABLE consent ADD CONSTRAINT consent_age_band_check
            CHECK (age_band IN ('adult', 'under_18'));
    END IF;
END $$;
