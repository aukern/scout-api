# Collections API

Collections are named partitions of knowledge. Every Source, Search, Question, and Session belongs to exactly one Collection.

**Avoid these terms when referring to Collections:** namespace, tenant, index, corpus.

---

## Endpoints

### POST /collections

Create a new collection with a unique name.

**Request body**

```json
{
  "name": "my-research"
}
```

| Field | Type | Rules |
|---|---|---|
| `name` | string | 1–200 chars; `[a-zA-Z0-9_\-]+` only |

**Responses**

| Status | Description |
|---|---|
| 201 | Collection created. Body contains `{id, name}`. `Location` header set to `/collections/{name}`. |
| 409 | A collection with this name already exists. |
| 422 | Validation error (name blank, too long, or contains invalid characters). |

**201 example**

```json
{
  "id": 1,
  "name": "my-research"
}
```

Headers:
```
Location: /collections/my-research
```

**409 example**

```json
{
  "error": {
    "code": "COLLECTION_ALREADY_EXISTS",
    "message": "A collection named 'my-research' already exists."
  }
}
```

---

### GET /collections

List all collections, ordered by creation time (oldest first).

**Responses**

| Status | Description |
|---|---|
| 200 | List of all collections. |

**200 example**

```json
{
  "collections": [
    {"id": 1, "name": "my-research"},
    {"id": 2, "name": "support-docs"}
  ],
  "total": 2
}
```

---

### DELETE /collections/{name}

Delete a collection by name. All Sources and Chunks belonging to this collection are also deleted (cascade).

**Path parameters**

| Parameter | Type | Description |
|---|---|---|
| `name` | string | Name of the collection to delete. |

**Responses**

| Status | Description |
|---|---|
| 204 | Collection deleted. No response body. |
| 404 | No collection with this name exists. |

**404 example**

```json
{
  "error": {
    "code": "COLLECTION_NOT_FOUND",
    "message": "Collection 'my-research' not found."
  }
}
```

---

## Error codes

| Code | HTTP | Meaning |
|---|---|---|
| `COLLECTION_ALREADY_EXISTS` | 409 | Duplicate collection name |
| `COLLECTION_NOT_FOUND` | 404 | No collection with this name |

---

## Cascade behavior

Deleting a collection is permanent and atomic. The database cascades the deletion to:

1. All **Sources** that belong to the collection
2. All **Chunks** that belong to those Sources (cascade from Sources)
3. All **Sessions** opened against the collection
4. All **Session activity** and **Briefs** within those Sessions

This is enforced via `ON DELETE CASCADE` foreign key constraints in the schema — no partial state is possible.
