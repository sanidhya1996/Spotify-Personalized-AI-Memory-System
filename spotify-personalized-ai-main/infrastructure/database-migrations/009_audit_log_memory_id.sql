-- Why this file exists
-- ====================
--
-- The audit log records which memory an action touched (abc.md:322 - logs
-- carry identifiers and outcomes). memory/db.py `record_audit` writes a
-- memory_id column, but 002_audit_log.sql never created it: the column was
-- added by hand to the first database and the migration was not updated.
--
-- A fresh database built only from these files - the deployed one on Neon -
-- therefore had no such column, and every audited request failed with
-- "column memory_id of relation audit_log does not exist".
--
-- Found by building a fresh database from the migrations and comparing every
-- column with the working local one; this was the only difference.
--
-- Safe to run again.

ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS memory_id TEXT;
