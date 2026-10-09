-- Why this file exists
-- ====================
--
-- One login per listener: a unique user id and a password, so a person can
-- only ever open their own account.
--
-- A pilot stand-in for Spotify's own login. abc.md assumes the gateway
-- verifies an existing Spotify session; this lets the pilot do the same job
-- without one.
--
-- Only a password HASH is stored (memory/accounts.py, scrypt with a random
-- salt), never the password itself. Nothing else about the person is kept
-- here - consent, region and age band are on the consent table.
--
-- Used by memory/accounts.py, which POST /auth/signup and POST /auth/login
-- call. Safe to run again.

CREATE TABLE IF NOT EXISTS account (
    subject_id     TEXT PRIMARY KEY,
    password_hash  TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
