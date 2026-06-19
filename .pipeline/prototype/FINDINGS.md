# POC Findings

## What the model covers
The full glossary surface from CONTEXT.md, walked in `poc.py`:
Collection · Source ingest · Source status lifecycle (pending → processing → ready → failed)
· Chunk · Embedding · re-ingest refresh-in-place · Collection-scoped Search · Result (score +
Source) · Question · Answer · Citation · Session (opt-in recording) · Brief.

Guarantees demonstrated with inline assertions: "not searchable until ready" (status gate),
re-ingest does not duplicate, failed Sources are excluded, and Collection isolation (ADR 0001).

Concept-critical, real: LiteLLM embeddings, pgvector cosine Search, Collection scoping, RAG
synthesis with Citations. The embedding dimension is derived from the model at setup, not
hardcoded. Left shallow / simulated (named in deps.py for auk-3): S3 upload, ARQ worker (run
inline), Redis Result cache, WebSocket streaming of Answers.

Run harness: docker-compose.yml provisions pgvector + Ollama (keyless/local default;
env-swappable to a hosted provider).

## Result
- [x] PASS — core flow works as expected (ran end-to-end, exit 0, all four guarantees held)
- [ ] FAIL — core flow does not work, see notes
- [ ] PARTIAL — works with caveats (list below)

Verified on real infrastructure: native Ollama (embeddings) + pgvector container + Gemini
(completion). Also re-run with a second, unseen document (octopus biology) — semantic ranking
and grounded citation both correct.

## Confirmed decisions

### LLM / AI
- Embeddings: `ollama/nomic-embed-text` (local, keyless) — 768-dim, derived at setup (not hardcoded).
- Completion: `gemini/gemini-2.5-flash-lite`. NOTE: on a fresh AI-Studio key, `gemini-2.0-flash`
  and `-2.0-flash-lite` returned 429 `limit: 0` (no free quota); `2.5-flash-lite` has free quota.
- Provider swap was env-only (no code change) — confirms the externals-are-pluggable design.
- Prompt that worked: "Answer using ONLY the numbered context. Cite sources inline as [1], [2].
  If insufficient, say so." → produced short, grounded answers citing `[1]`.
- Output shape: 1–2 sentence Answer + a list of Source URLs as Citations.

### Database
- Schema shape: collections / sources(unique collection_id+origin) / chunks(vector(768)) /
  sessions / session_activity / briefs.
- Key query: Collection-scoped `embedding <=> $q ... WHERE collection_id=$1 AND status='ready'`
  ranked correctly (closest chunk was the semantically-relevant one, not keyword overlap).

### Functional surface
- Happy path ran clean: ingest → process → ready → scoped search → Q&A with citations → session → brief.
- Capabilities covered: all glossary entities exercised.
- Left shallow / deferred to auk-4: S3 upload, ARQ queue, Redis cache, WebSocket streaming.
- Edge cases held: status gate (0 results while pending), isolation (no cross-Collection leak),
  re-ingest (no duplicate chunks), failed Source excluded from Search — all asserted inline.

## What was tried and rejected
- `gemini-2.0-flash` / `gemini-2.0-flash-lite`: no free-tier quota on a new key (429 limit:0).
- `gemini-1.5-flash`: 404, deprecated/unavailable on this key.
- Local completion (`ollama/llama3.2`): skipped on this low-RAM box (~5GB); used hosted Gemini instead.

## Open questions for auk-3
- Chunking strategy (current is a naive ~200-char packer — production wants token-aware overlap).
- Embedding model/dimension as config (768 local vs 1536 hosted — schema must follow the model).
- Citation granularity: cite the Source, or the specific Chunk within it?
- Session retention / cleanup policy; failed-Source retry policy.
