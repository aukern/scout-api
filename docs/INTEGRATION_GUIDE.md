# Integration Guide

> For teams integrating with Scout API — frontend developers, partner services, or agents consuming the API. Covers the public API surface, error handling, and local test setup.

For internal architecture and contribution, see [DEVELOPMENT.md](../DEVELOPMENT.md).
For deployment and operations, see [HANDOFF.md](../HANDOFF.md).

## Table of Contents

- [Base URL](#base-url)
- [Authentication](#authentication)
- [Endpoints](#endpoints)
- [Request / Response Format](#request--response-format)
- [Error Codes](#error-codes)
- [Environment Variables for Integration Testing](#environment-variables-for-integration-testing)
- [OpenAPI Spec](#openapi-spec)

---

## Base URL

| Environment | Base URL |
|-------------|----------|
| Local dev   | `http://localhost:8000` |
| Staging     | Set in your deployment config |
| Production  | Set in your deployment config |

---

## Authentication

No authentication required for this version. All endpoints are open. Authentication is planned for a future slice.

---

## Endpoints

Interactive API explorer (local dev):
```
http://localhost:8000/docs         — Swagger UI
http://localhost:8000/redoc        — ReDoc
http://localhost:8000/openapi.json — Raw OpenAPI spec
```

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/collections` | Create a new collection |
| `GET` | `/collections` | List all collections |
| `DELETE` | `/collections/{name}` | Delete a collection (cascades to Sources and Chunks) |
| `POST` | `/collections/{collection_id}/sources/url` | Ingest a URL into a collection |
| `POST` | `/collections/{collection_id}/sources/file` | Upload a file into a collection |
| `POST` | `/sessions` | Open a research session |
| `GET` | `/sessions` | List sessions |
| `GET` | `/sessions/{session_id}` | Get session details |
| `DELETE` | `/sessions/{session_id}` | Delete a session |

---

## Request / Response Format

All endpoints accept and return `application/json` (file upload uses `multipart/form-data`).

**Source ingest — URL:**

```bash
curl -X POST http://localhost:8000/collections/1/sources/url \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/paper.pdf"}'
# → {"id": 42, "status": "pending", "origin": "https://example.com/paper.pdf"}
```

**Error response:**

```json
{
  "error": "SRC_NF_001",
  "message": "Collection 99 not found.",
  "retryable": false
}
```

**Source processing** is asynchronous. After ingest, poll the source status or subscribe to domain events:
- `source.processing_started` — worker picked up the job
- `source.ready` — chunks embedded, source is searchable
- `source.failed` — processing failed; `failed_reason` is set in the database

---

## Error Codes

| Code | Type | Message | Retryable | When |
|------|------|---------|-----------|------|
| `SRC_NF_001` | `CollectionNotFoundError` | Collection {id} not found. | No | POST /sources — collection_id does not exist |
| `SRC_ING_001` | `SourceIngestionError` | Source ingestion failed: {detail} | Yes | S3 upload or job enqueue failed |
| `SRC_VAL_001` | `InvalidOriginError` | Invalid origin: {detail} | No | Empty filename or invalid URL |
| `SES_NF_001` | `SessionNotFoundError` | Session {session_id} not found. | No | GET or DELETE /sessions/{id} |
| `SES_NF_002` | `SessionCollectionNotFoundError` | Collection {collection_id} not found. | No | Collection not found when opening a session |
| `SRC_PROC_001` | `SourceProcessingError` | Processing failed for source {source_id}: {detail} | No | Source processing failed during fetch, chunk, or embed step |
| `SRC_PROC_002` | `EmbeddingDimensionMismatchError` | Embedding dimension mismatch | No | Model probe dimension does not match the schema column — run migration 003 |
| `SRC_PROC_003` | `SourceNotFoundError` | Source {source_id} not found | No | Worker cannot find the source to process |

**General rules:**
- `4xx` — client error; fix the request. Not retryable unless the error says otherwise.
- `5xx` — server error; may be transient. Check `retryable` field before retrying.
- Retry with exponential backoff: start at 1s, cap at 30s, max 3 attempts.

Full error code reference: [`docs/api/ERROR_CODES.md`](api/ERROR_CODES.md)

---

## Environment Variables for Integration Testing

To run integration tests against a local instance:

```bash
cp .env.example .env
# Fill in .env
docker compose --profile postgres --profile redis --profile worker up -d
make test-integration
```

Required variables:

| Variable | Required | Purpose |
|----------|----------|---------|
| `DATABASE_URL` | Yes | Postgres connection string |
| `REDIS_URL` | Yes | Redis DSN for arq job queue |
| `AWS_ACCESS_KEY_ID` | Yes (S3 tests) | AWS credential for S3 uploads |
| `AWS_SECRET_ACCESS_KEY` | Yes (S3 tests) | AWS secret key |
| `S3_BUCKET_NAME` | Yes (S3 tests) | S3 bucket for source file uploads |
| `S3_ENDPOINT_URL` | No | S3 endpoint override for localstack (`http://localhost:4566`) |
| `EMBEDDING_MODEL` | No | LiteLLM model string (default: `text-embedding-ada-002`) |

---

## OpenAPI Spec

The service exposes a machine-readable OpenAPI spec at `/openapi.json`.

**Generate a typed client:**

```bash
# Python (openapi-python-client)
pip install openapi-python-client
openapi-python-client generate --url http://localhost:8000/openapi.json

# TypeScript (openapi-typescript)
npx openapi-typescript http://localhost:8000/openapi.json -o types.ts
```
