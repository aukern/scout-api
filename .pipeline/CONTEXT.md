# Scout API

The tool layer for AI research agents: ingests knowledge, runs semantic search, and answers questions over what it has ingested. Decoupled from any specific agent — any agent that needs knowledge ingestion and search wires to this API.

## Language

### Ingestion

**Collection**:
A named partition of knowledge. Every Source belongs to exactly one Collection, and Searches and Questions are scoped to a Collection. This is how the shared tool server keeps one agent's (or topic's) knowledge isolated from another's.
_Avoid_: Namespace, tenant, index, corpus

**Source**:
Any single item ingested into Scout — a URL or an uploaded file. It is the entity that gets chunked, embedded, and later cited. Origin (URL vs file) is an attribute of a Source, not a separate kind of thing. A Source is unique within its Collection by origin: re-ingesting the same URL or file refreshes the existing Source in place (re-chunk, re-embed) rather than creating a duplicate.
_Avoid_: Document, file, URL, content, artifact

**Chunk**:
A contiguous slice of a Source's content, sized for embedding and retrieval. A Source has many Chunks.

**Source status**:
The lifecycle state of a Source. A Source is searchable only once **ready**.
- **pending** — accepted and queued, processing not started
- **processing** — being chunked and embedded
- **ready** — fully embedded and searchable
- **failed** — processing failed and was abandoned

**Embedding**:
The vector representation of a Chunk, stored for semantic search.

### Retrieval

**Search**:
A semantic lookup, scoped to one Collection, that returns ranked **Results**. Input is free text; output is matching Chunks ordered by relevance. A Search never synthesizes — it only retrieves.
_Avoid_: Query (ambiguous between Search and Question)

**Result**:
A single ranked Chunk returned by a Search, carrying its relevance score and its originating Source.

**Question**:
A natural-language ask submitted to Q&A, scoped to one Collection. It triggers retrieval followed by LLM synthesis, producing an **Answer**.
_Avoid_: Query, prompt

**Answer**:
The synthesized response to a Question, streamed back with **Citations** to the Sources it drew from.

**Citation**:
A reference within an Answer pointing to the specific Source (and Chunk) that supports a claim.

### Sessions

**Session**:
A research workspace, opened against exactly one Collection, that records an activity trail: every Search run, every Question asked with its Answer, and which Answers were saved as Briefs. The unit of grouping for a line of research. All activity in a Session is scoped to that Session's Collection. Recording is opt-in — a Search or Question is recorded only when it is associated with a Session; otherwise it runs transiently and leaves no trace.

**Brief**:
A saved Answer kept within a Session for later reference — the Answer text plus its Citations, marked as saved. A Session has many Briefs; a Brief belongs to exactly one Session.
_Avoid_: Summary, note, report

## Example dialogue

> **Agent dev:** My agent ingests a paper URL, then immediately searches for it and gets nothing back. Bug?
>
> **Scout dev:** Not a bug. Ingest creates a **Source** in **pending**, then a worker takes it to **processing** and finally **ready**. A Source is only searchable once it's **ready** — your **Search** ran before the **Embeddings** existed.
>
> **Agent dev:** Got it. And if I ingest the same URL again later to refresh it?
>
> **Scout dev:** Same Source — within a **Collection** a URL is unique, so we re-chunk and re-embed it in place. No duplicate **Results**.
>
> **Agent dev:** Speaking of Collections — my research agent and the support agent both use Scout. Will my Searches see the support docs?
>
> **Scout dev:** No. Every Search and **Question** is scoped to one Collection. Put your papers in their own Collection and they stay isolated.
>
> **Agent dev:** Last thing — I want to keep the good answers. Difference between an Answer and a Brief?
>
> **Scout dev:** An **Answer** is what Q&A streams back to a Question, with **Citations**. It's transient unless you're in a **Session**. When you save an Answer into a Session, it becomes a **Brief** — that's the durable, kept version.
