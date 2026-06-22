-- 002_session_activity_output.sql
-- Adds the output column to session_activity, missing from the initial schema.
--
-- The initial schema (001) created session_activity without an output column.
-- The prototype stored both input (renamed to query) and output. This migration
-- adds output as a nullable TEXT column — historical rows without output remain
-- valid; new rows from slices 5 (search results as JSON) and 6 (answer text)
-- will populate it.
--
-- This is an additive, non-breaking change.

ALTER TABLE session_activity ADD COLUMN IF NOT EXISTS output TEXT;
