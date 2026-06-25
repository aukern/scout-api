-- Migration 005: IVFFlat index on chunks.embedding for cosine NN search
--
-- An IVFFlat approximate nearest-neighbour index dramatically reduces query
-- time for large chunk tables at the cost of a small accuracy loss.
--
-- lists = 100 is a safe default for tables up to ~1M rows. Rule of thumb:
--   lists ≈ sqrt(row_count) for optimal recall.
--   For < 1M rows: 100 is conservative and always safe.
--   For > 1M rows: recalculate and rebuild with more lists.
--
-- The index is non-destructive and idempotent (IF NOT EXISTS). Exact NN
-- search via <=> always works without this index — the index only improves
-- performance at scale.
--
-- Requires: pgvector extension (already enabled by 001_initial_schema.sql)

CREATE INDEX IF NOT EXISTS chunks_embedding_cosine_idx
    ON chunks
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
