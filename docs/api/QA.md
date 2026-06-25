# QA API Reference

The QA domain provides grounded question-answering over Collections via a
WebSocket endpoint and an MCP tool. Both surfaces use the same `QAService`
internally — no duplicate retrieval logic.

---

## WebSocket endpoint

### `WebSocket /collections/{collection_id}/qa`

Stream a grounded answer to a question against a Collection.

**Transport:** WebSocket (JSON frames).

**Protocol:**

1. Client connects and sends one JSON message:

```json
{"question": "What is the Scout API?", "top_k": 10, "session_id": null}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `question` | string | Yes | Natural-language question. 1–4000 characters. |
| `top_k` | integer | No | Number of chunks to retrieve (default 10, range 1–100). |
| `session_id` | integer or null | No | Session ID for recording the Q&A exchange. |

2. Server streams token frames:

```json
{"type": "token", "text": "Scout API is "}
{"type": "token", "text": "a tool layer for AI agents [1]."}
```

3. Server sends a done frame when synthesis completes:

```json
{
  "type": "done",
  "citations": [
    {
      "source_id": 3,
      "source_origin": "https://docs.example.com/guide",
      "chunk_ids": [12, 14],
      "inline_marker": "[1]"
    }
  ]
}
```

4. On error, the server sends an error frame:

```json
{"type": "error", "code": "QA_COL_001", "message": "Collection 42 not found"}
```

Validation failures also close the WebSocket with code 4000.

**Retrieval scope:** Only `ready` Sources within `collection_id` contribute to
the answer. Sources still processing are excluded.

**Grounding guarantee:** The LLM prompt instructs the model to answer using
only the numbered sources. When context is insufficient, the answer contains
"I don't have enough information to answer that." — the model does not fabricate.

**Session recording:** When `session_id` is provided and the request includes a
valid session, the question and full synthesized answer are recorded in
`session_activity` as `kind="question"`. Recording failure is non-fatal — a
warning is logged but the answer is still delivered.

---

## Error codes

| Code | Condition | WebSocket behaviour |
|---|---|---|
| `QA_VAL_001` | Question empty or exceeds 4000 characters | Error frame + close(4000) |
| `QA_COL_001` | Collection not found | Error frame, connection closed |
| `QA_CTX_001` | No ready chunks in collection | Error frame, connection closed |
| `QA_SYN_001` | LLM call failed (network, timeout, content filter) | Error frame, connection closed |

---

## MCP Tools

The QA domain exposes one tool to AI agents via the FastMCP server mounted at
`/mcp/qa`.

**Server instructions** (shown to AI agents on connect):

> Provides question-answering over Collections of ingested knowledge.
> Use ask_collection to get a grounded answer with inline citations.
> Only ready sources contribute to the answer context.
> When context is insufficient the tool returns an explicit statement
> rather than fabricating an answer.

**How to connect:**

```python
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async with streamablehttp_client("http://localhost:8000/mcp/qa") as (r, w, _):
    async with ClientSession(r, w) as session:
        await session.initialize()
        result = await session.call_tool("ask_collection", {"collection_id": 1, "question": "What is Scout?"})
```

---

### `ask_collection`

**Title:** Ask Collection
**Annotations:** readOnly=true, destructive=false, idempotent=false, openWorld=false

Ask a question against a Collection. Returns the full synthesized answer and
citations. Use this when an agent needs a grounded, synthesized answer from
indexed content rather than raw chunk text. For raw chunks, use
`search_collection` instead.

**Parameters:**

| Parameter | Type | Required | Constraints | Description |
|---|---|---|---|---|
| `collection_id` | integer | Yes | > 0 | The integer ID of the Collection to ask about. Only chunks from ready sources within this collection are used. |
| `question` | string | Yes | 1–4000 chars | The natural-language question to answer. Grounded exclusively in the collection's indexed content. |
| `top_k` | integer | No | 1–100, default 10 | Number of chunks to retrieve for synthesis context. |

**Returns:**

```json
{
  "answer": "Scout API is a tool layer for AI agents [1].",
  "citations": [
    {
      "source_id": 3,
      "source_origin": "https://docs.example.com",
      "chunk_ids": [12, 14],
      "inline_marker": "[1]"
    }
  ]
}
```

| Field | Type | Description |
|---|---|---|
| `answer` | string | Full synthesized answer text with inline [N] citation markers. |
| `citations` | list | Cited sources in order of first appearance. |
| `citations[].source_id` | integer | PK of the cited Source. |
| `citations[].source_origin` | string | URL or S3 path of the cited Source. |
| `citations[].chunk_ids` | list[int] | Chunk PKs that contributed to this source's prompt entry. |
| `citations[].inline_marker` | string | The literal [N] marker used in the answer text. |

**Error handling:** All domain errors are raised as `ToolError` with the error
message. The tool does not throw on insufficient context — the answer text will
contain the insufficient-context phrase instead.

---

## Domain types

### `Question`

```python
@dataclass(frozen=True)
class Question:
    collection_id: int   # Collection to scope retrieval to
    text: str            # Natural-language question (1–4000 chars)
    top_k: int = 10      # Number of chunks to retrieve
```

### `Citation`

```python
@dataclass(frozen=True)
class Citation:
    source_id: int        # PK of the cited Source
    source_origin: str    # URL or S3 path for display
    chunk_ids: list[int]  # Chunk PKs contributing to this source
    inline_marker: str    # Literal marker in answer, e.g. "[1]"
```

### `AnswerChunk`

```python
@dataclass(frozen=True)
class AnswerChunk:
    text: str                    # Incremental LLM token
    is_final: bool = False       # True on last chunk only
    citations: list[Citation]    # Populated only when is_final=True
```
