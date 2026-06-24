# Sources API

Accept knowledge into a collection. Sources are URLs (fetched later) or uploaded files
(stored immediately in S3). Processing happens asynchronously — the API returns immediately.

---

## POST /collections/{collection_id}/sources/url

Ingest a URL into a collection. The URL document is not fetched at ingest time — a
background job is enqueued to fetch and process it.

**Re-ingest**: If the same URL is posted to the same collection again, the existing
Source is refreshed in place. Old chunks are deleted and a new processing job is enqueued.
The response always returns 201 with the current source state.

### Request

```
POST /collections/{collection_id}/sources/url
Content-Type: application/json
```

```json
{ "url": "https://example.com/document.pdf" }
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `url` | string (HttpUrl) | yes | Valid HTTP or HTTPS URL |

### Response — 201 Created

```json
{
  "id": 42,
  "collection_id": 7,
  "origin": "https://example.com/document.pdf",
  "status": "pending"
}
```

**Headers**: `Location: /collections/7/sources/42`

### Error responses

| Status | Code | When |
|--------|------|------|
| 404 | `SRC_NF_001` | collection_id does not exist |
| 422 | _(Pydantic)_ | url is not a valid HTTP/HTTPS URL |
| 500 | `SRC_ING_001` | Background job could not be enqueued |

---

## POST /collections/{collection_id}/sources/file

Upload a file into a collection. The file bytes are stored in S3 immediately;
processing (text extraction, chunking, embedding) happens asynchronously.

**Re-ingest**: If a file with the same filename is uploaded to the same collection,
the existing Source is refreshed in place. Old chunks are deleted, the file is
re-uploaded to S3, and a new processing job is enqueued.

### Request

```
POST /collections/{collection_id}/sources/file
Content-Type: multipart/form-data
```

| Form field | Type | Required | Description |
|------------|------|----------|-------------|
| `file` | binary | yes | File to ingest. Must not be empty. |

### Response — 201 Created

```json
{
  "id": 43,
  "collection_id": 7,
  "origin": "file://7/a1b2c3d4/report.pdf",
  "status": "pending"
}
```

**Headers**: `Location: /collections/7/sources/43`

### Error responses

| Status | Code | When |
|--------|------|------|
| 404 | `SRC_NF_001` | collection_id does not exist |
| 422 | _(HTTPException)_ | File is empty or has no filename |
| 500 | `SRC_ING_001` | S3 upload or background job enqueue failed |

---

---

## GET /collections/{collection_id}/sources

List all sources in a collection with their current lifecycle status.
Results are ordered by creation time (oldest first).

### Response — 200 OK

```json
{
  "sources": [
    {
      "id": 42,
      "collection_id": 7,
      "origin": "https://example.com/doc.pdf",
      "status": "ready",
      "created_at": "2024-01-01T12:00:00Z",
      "updated_at": "2024-01-01T12:05:00Z",
      "failed_reason": null
    }
  ],
  "total": 1
}
```

Returns an empty list (`"sources": [], "total": 0`) when the collection exists but has no sources.

### Error responses

| Status | Code | When |
|--------|------|------|
| 404 | `SRC_NF_001` | collection_id does not exist |

---

## GET /collections/{collection_id}/sources/{source_id}

Fetch a single source by ID. The source must belong to the given collection —
cross-collection lookups return 404 (not the source data).

### Response — 200 OK

```json
{
  "id": 42,
  "collection_id": 7,
  "origin": "https://example.com/doc.pdf",
  "status": "failed",
  "created_at": "2024-01-01T12:00:00Z",
  "updated_at": "2024-01-01T12:03:00Z",
  "failed_reason": "HTTP 403 fetching origin URL"
}
```

`failed_reason` is `null` when status is not `failed`.

### Error responses

| Status | Code | When |
|--------|------|------|
| 404 | `SRC_NF_002` | source_id not found, or belongs to a different collection |

---

## Source lifecycle

```
pending  →  processing  →  ready
                       ↘  failed
```

| Status | Meaning |
|--------|---------|
| `pending` | Created, waiting for the worker to pick it up |
| `processing` | Worker is actively processing this source |
| `ready` | Chunks are created and embedded — source is searchable |
| `failed` | Processing failed; will be retried by the reaper job |

---

## Origin format

| Source type | Origin format | Example |
|-------------|---------------|---------|
| URL | Raw URL string | `https://example.com/doc.pdf` |
| File upload | `file://{collection_id}/{hash}/{filename}` | `file://7/a1b2c3d4/report.pdf` |

The origin is the stable identity for re-ingest detection. The same origin posted
twice to the same collection is a refresh — not a duplicate.
