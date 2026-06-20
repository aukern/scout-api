-- 001_initial_schema.sql
-- Creates the full Scout API schema.
--
-- All tables are created here even if only collections is used in slice 17.
-- Reasons:
--   1. ON DELETE CASCADE references downstream tables that must exist for FKs to be valid.
--   2. Later slices will not need their own base-schema migrations.
--   3. Matches the prototype pattern where DROP/CREATE ran together.
--
-- Enable the pgvector extension for embeddings (used in the search slices).
-- This is a no-op if the extension is already installed.

CREATE EXTENSION IF NOT EXISTS vector;

-- ── Collections ───────────────────────────────────────────────────────────────
-- A named partition of knowledge. Every Source belongs to one Collection.
-- Avoid: namespace, tenant, index, corpus.

CREATE TABLE IF NOT EXISTS collections (
    id   SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL
);

-- ── Sources ───────────────────────────────────────────────────────────────────
-- Any single item ingested into Scout — a URL or an uploaded file.
-- A Source is unique within its Collection by origin (re-ingest = refresh).

CREATE TABLE IF NOT EXISTS sources (
    id            SERIAL PRIMARY KEY,
    collection_id INTEGER NOT NULL
        REFERENCES collections(id) ON DELETE CASCADE,
    origin        TEXT NOT NULL,                  -- URL or file path
    status        TEXT NOT NULL DEFAULT 'pending' -- pending | processing | ready | failed
        CHECK (status IN ('pending', 'processing', 'ready', 'failed')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (collection_id, origin)
);

-- ── Chunks ────────────────────────────────────────────────────────────────────
-- A contiguous slice of a Source's content, sized for embedding and retrieval.

CREATE TABLE IF NOT EXISTS chunks (
    id        SERIAL PRIMARY KEY,
    source_id INTEGER NOT NULL
        REFERENCES sources(id) ON DELETE CASCADE,
    content   TEXT NOT NULL,
    position  INTEGER NOT NULL,                   -- order within the source
    embedding vector(1536)                        -- set once the chunk is embedded
);

-- ── Sessions ─────────────────────────────────────────────────────────────────
-- A research workspace scoped to one Collection.

CREATE TABLE IF NOT EXISTS sessions (
    id            SERIAL PRIMARY KEY,
    collection_id INTEGER NOT NULL
        REFERENCES collections(id) ON DELETE CASCADE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Session activity ─────────────────────────────────────────────────────────
-- Records Searches and Questions run within a Session.

CREATE TABLE IF NOT EXISTS session_activity (
    id         SERIAL PRIMARY KEY,
    session_id INTEGER NOT NULL
        REFERENCES sessions(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL CHECK (kind IN ('search', 'question')),
    query      TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Briefs ────────────────────────────────────────────────────────────────────
-- A saved Answer kept within a Session for later reference.

CREATE TABLE IF NOT EXISTS briefs (
    id          SERIAL PRIMARY KEY,
    session_id  INTEGER NOT NULL
        REFERENCES sessions(id) ON DELETE CASCADE,
    answer_text TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
