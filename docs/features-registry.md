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


## Slice 24 — Ask a question, stream a cited answer

**Module:** `scout_api.qa`

**Purpose:** Grounded question-answering over a Collection. Retrieves the top_k most relevant chunks via pgvector cosine similarity, builds a numbered-source prompt, and streams a LiteLLM completion token-by-token. Citations are extracted from [N] markers in the accumulated answer text and delivered in the final frame. The LLM is grounded — it answers from indexed sources only and states explicitly when context is insufficient.

**WebSocket endpoint:**
- `WebSocket /collections/{collection_id}/qa` — stream a cited answer to a question

**MCP tool:**
- `ask_collection(collection_id, question, top_k)` — request/response QA callable by AI agents; collects all tokens before returning

**Key types:**
- `Question` — frozen dataclass: collection_id, text, top_k
- `Citation` — frozen dataclass: source_id, source_origin, chunk_ids, inline_marker (source-level granularity)
- `AnswerChunk` — frozen dataclass: text, is_final, citations (citations only on final chunk)

**Error codes:**
- `QA_COL_001` — collection not found (404)
- `QA_CTX_001` — no ready chunks available in collection (422)
- `QA_SYN_001` — LLM synthesis failed: network, timeout, or content filter (502)
- `QA_VAL_001` — question empty or exceeds 4000 characters (400)

**Session recording:** When `session_id` is provided, the question and full answer are recorded in `session_activity` as `kind="question"`. Non-fatal — recording failure is logged as a warning.

**Observability:** `@observed("qa.ask")` metric, `tracer.start_as_current_span("qa.ask")` with collection_id/top_k/citation_count attributes, `question.answered` domain event after stream completes.

**Eval cases:** `config/evals/qa_synthesis.jsonl` — 5 cases covering single source, multi-source, insufficient context, collection scoping, and long question.
