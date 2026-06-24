-- 004_briefs_citations.sql
-- Adds citations JSONB column to briefs table.
-- The initial schema (001) created briefs with only answer_text.
-- Citations (source references) are stored as a JSON array — nullable
-- for backward compatibility if a Brief is created with no citations.
--
-- Citation shape: {"source_id": 1, "chunk_id": 42, "excerpt": "..."}
-- chunk_id and excerpt are optional.

ALTER TABLE briefs ADD COLUMN IF NOT EXISTS citations JSONB;
