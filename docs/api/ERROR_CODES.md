# Error Codes

All Scout API errors follow the same envelope format:

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable description of what went wrong."
  }
}
```

---

## Collections

| Code | HTTP | When |
|---|---|---|
| `COLLECTION_ALREADY_EXISTS` | 409 | POST /collections with a name already in use |
| `COLLECTION_NOT_FOUND` | 404 | DELETE /collections/{name} where name does not exist |

---

## Standard HTTP errors

| Status | When |
|---|---|
| 422 | Request body fails Pydantic validation (missing field, wrong type, pattern mismatch) |
| 500 | Unhandled internal error (database unreachable, unexpected exception) |
