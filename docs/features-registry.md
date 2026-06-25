# Features Registry


## Slice 21 — Browse sources & their status

**Capability:** sources domain module


## Slice 22 — Save answers as briefs

**Module:** `scout_api.briefs`

**Purpose:** Save an Answer (text + Citations) as a durable Brief within a Research Session. The Brief is the kept, named version of an otherwise transient answer.

**Endpoints:**
- `POST /sessions/{session_id}/briefs` — Save an Answer as a Brief; returns 201 + Location
- `GET /sessions/{session_id}/briefs` — List all Briefs in a session, oldest first

**Key types:**
- `BriefCitation` — value object linking to a Source (source_id, optional chunk_id, optional excerpt)
- `BriefRow` — frozen dataclass: id, session_id, answer_text, citations, created_at

**Error codes:** BRF_NF_001 (session not found), BRF_NF_002 (brief not found — reserved)

**Migration:** `004_briefs_citations.sql` — adds `citations JSONB` column to briefs table


## Slice 23 — Semantic search within a collection

**Module:** `scout_api.search`

**Purpose:** Free-text semantic search scoped to a Collection. Embeds the query via LiteLLM, executes a pgvector cosine nearest-neighbour query filtered to `ready` Sources, and returns ranked Chunks with relevance scores. Repeated identical queries are served from a Redis cache.

**Endpoints:**
- `POST /collections/{collection_id}/search` — search within a collection; returns ranked chunks with scores

**MCP tool:**
- `search_collection(collection_id, query, top_k)` — semantic search callable by AI agents

**Key types:**
- `SearchResult` — frozen dataclass: chunk_id, source_id, collection_id, content, score, source_origin
- `SearchQuery` — frozen dataclass: collection_id, query_text, top_k

**Error codes:**
- `SEARCH_COL_001` — collection not found (404)
- `SEARCH_EMB_001` — embedding model call failed (502)

**Cache:** Redis key `search:{collection_id}:{sha256(normalized_query)}`, TTL 5 min. Invalidated on `source.ready` event.

**Migration:** `005_search_index.sql` — IVFFlat index on `chunks.embedding` for production-scale cosine NN performance
