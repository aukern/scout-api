# Sessions API

A Session is a research workspace opened against exactly one Collection. It records an activity trail of every Search and Question run within it. Recording is opt-in — a Search or Question only appears in the trail if a `session_id` was supplied; otherwise it runs transiently and leaves no record.

---

## Endpoints

### POST /sessions

Open a new session against a collection.

**Request body**

```json
{
  "collection_id": 1
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `collection_id` | integer | yes | ID of the collection to scope this session to |

**Responses**

| Status | Description |
|---|---|
| `201 Created` | Session opened. Location header points to the new session. |
| `404 Not Found` | Collection with the given `collection_id` does not exist. Error code: `SES_NF_002`. |
| `422 Unprocessable Entity` | Request body validation failed. |

**Example response (201)**

```json
{
  "id": 7,
  "collection_id": 1,
  "created_at": "2024-01-15T10:00:00Z"
}
```

**Location header**

```
Location: /sessions/7
```

---

### GET /sessions

List all sessions, optionally filtered by collection.

**Query parameters**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `collection_id` | integer | no | If provided, return only sessions for this collection |

**Responses**

| Status | Description |
|---|---|
| `200 OK` | Returns all matching sessions. |

**Example response (200)**

```json
{
  "sessions": [
    {
      "id": 7,
      "collection_id": 1,
      "created_at": "2024-01-15T10:00:00Z"
    }
  ],
  "total": 1
}
```

---

### GET /sessions/{session_id}

Fetch a session and its full activity trail.

**Path parameters**

| Parameter | Type | Description |
|---|---|---|
| `session_id` | integer | ID of the session to fetch |

**Responses**

| Status | Description |
|---|---|
| `200 OK` | Returns the session with its activity trail. |
| `404 Not Found` | Session does not exist. Error code: `SES_NF_001`. |

**Example response (200)**

```json
{
  "id": 7,
  "collection_id": 1,
  "created_at": "2024-01-15T10:00:00Z",
  "activity": [
    {
      "id": 10,
      "kind": "search",
      "query": "machine learning papers 2024",
      "output": null,
      "created_at": "2024-01-15T10:01:00Z"
    },
    {
      "id": 11,
      "kind": "question",
      "query": "What are the key findings?",
      "output": "The key findings are ...",
      "created_at": "2024-01-15T10:02:00Z"
    }
  ]
}
```

Activity items appear in chronological order (oldest first). `output` is `null` for searches where results were not serialised, or for questions without an answer yet.

---

### DELETE /sessions/{session_id}

Close (delete) a session. All activity is also removed.

**Path parameters**

| Parameter | Type | Description |
|---|---|---|
| `session_id` | integer | ID of the session to close |

**Responses**

| Status | Description |
|---|---|
| `204 No Content` | Session closed successfully. No body. |
| `404 Not Found` | Session does not exist. Error code: `SES_NF_001`. |

---

## Error Codes

| Code | HTTP | When |
|---|---|---|
| `SES_NF_001` | 404 | Session not found (GET or DELETE) |
| `SES_NF_002` | 404 | Collection not found when opening a session (POST) |

---

## Opt-In Recording Contract

Slices 5 (Search) and 6 (Questions) write to a Session's activity trail by calling `SessionActivityRepository.record()` — but only when a `session_id` is present in their request. When no `session_id` is given, the operation runs transiently and nothing is recorded.

This means a session's activity trail reflects exactly the operations the caller chose to record — no automatic tracking.

The recording interface is a published Protocol:

```python
# src/scout_api/sessions/contracts.py
class SessionActivityRepositoryProtocol(Protocol):
    async def record(
        self,
        session_id: int,
        kind: Literal["search", "question"],
        query: str,
        output: str | None,
        conn: asyncpg.Connection,
    ) -> SessionActivityRow: ...
```

Slices 5 and 6 import this Protocol — not the implementation — keeping the session module as the sole owner of the SQL.
