# .pipeline/prototype/poc.py
#
# THROWAWAY — scale-model prototype. Not production code.
# Goal: exercise the FULL functional surface from CONTEXT.md — every entity and
# behavior, shallow but real — so auk-3 can see everything it must turn into issues.
# Concept-critical capabilities use real tech; production plumbing is simulated inline.
#
# Coverage (CONTEXT.md glossary + ADR 0001):
#   Collection · Source ingest · Source status (pending→processing→ready→failed)
#   · Chunk · Embedding · re-ingest-refresh-in-place · Collection-scoped Search
#   · Result (score + Source) · Collection isolation · "not searchable until ready"
#   · Question · Answer · Citation · Session (opt-in recording) · Brief
#
# Concept-critical (real): embeddings, pgvector cosine search, Collection scoping,
#   RAG synthesis with citations.
# Plumbing (named in deps.py, simulated here): S3 upload, ARQ worker (run inline),
#   Redis cache, WebSocket streaming.
#
# Run (keyless/local externals via the harness):
#   docker compose -f .pipeline/prototype/docker-compose.yml up -d
#   docker compose -f .pipeline/prototype/docker-compose.yml exec ollama ollama pull nomic-embed-text
#   docker compose -f .pipeline/prototype/docker-compose.yml exec ollama ollama pull llama3.2
#   python -m venv .venv && . .venv/bin/activate
#   pip install -r .pipeline/prototype/requirements.txt
#   python .pipeline/prototype/poc.py
#
# When done: fill in FINDINGS.md, commit. This file is the source of truth for build AND updates.

import asyncio
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])  # allow: from deps import ...

from deps import complete, embed, get_db, to_pgvector

# ── Fixture content (ingested inline; production would fetch the URL / read the file) ──
RUST_OWNERSHIP = """
Ownership is Rust's most unique feature and has deep implications for the language.
Each value in Rust has a single owner, and there can only be one owner at a time.
When the owner goes out of scope, the value is dropped and its memory is freed automatically.
Borrowing lets you reference a value without taking ownership, via shared or mutable references.
"""

SOURDOUGH = """
A sourdough starter is a stable culture of wild yeast and lactobacilli in flour and water.
Feed the starter with equal parts flour and water on a regular schedule to keep it active.
"""


# ── Domain logic (business logic lives in poc.py; deps.py is external systems only) ──

def chunk_text(text: str, size: int = 200) -> list[str]:
    """Naive chunker: pack non-empty lines into ~size-char Chunks."""
    pieces, current = [], ""
    for line in (ln.strip() for ln in text.splitlines()):
        if not line:
            continue
        if current and len(current) + len(line) + 1 > size:
            pieces.append(current.strip())
            current = ""
        current += " " + line
    if current.strip():
        pieces.append(current.strip())
    return pieces


async def ensure_collection(db, name: str) -> int:
    """A Collection is a named partition of knowledge (ADR 0001)."""
    return await db.fetchval(
        "INSERT INTO collections (name) VALUES ($1) "
        "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
        name,
    )


async def ingest_source(db, collection_id: int, origin: str) -> int:
    """Accept a Source (URL or file) into a Collection, status=pending.

    Unique within its Collection by origin: re-ingesting the same origin refreshes the
    existing Source in place (drop old Chunks) rather than duplicating. In production the
    file/URL would first be uploaded to S3 (plumbing).
    """
    source_id = await db.fetchval(
        """INSERT INTO sources (collection_id, origin, status)
           VALUES ($1, $2, 'pending')
           ON CONFLICT (collection_id, origin) DO UPDATE SET status = 'pending'
           RETURNING id""",
        collection_id, origin,
    )
    await db.execute("DELETE FROM chunks WHERE source_id = $1", source_id)  # refresh in place
    return source_id


async def process_source(db, source_id: int, content: str, fail: bool = False) -> None:
    """Simulate the ARQ background worker: pending → processing → ready (or failed)."""
    await db.execute("UPDATE sources SET status = 'processing' WHERE id = $1", source_id)
    try:
        if fail:
            raise RuntimeError("simulated processing failure")
        collection_id = await db.fetchval("SELECT collection_id FROM sources WHERE id = $1", source_id)
        pieces = chunk_text(content)
        vectors = await embed(pieces)  # Chunk → Embedding
        for piece, vector in zip(pieces, vectors):
            await db.execute(
                "INSERT INTO chunks (source_id, collection_id, content, embedding) "
                "VALUES ($1, $2, $3, $4)",
                source_id, collection_id, piece, to_pgvector(vector),
            )
        await db.execute("UPDATE sources SET status = 'ready' WHERE id = $1", source_id)
    except Exception:
        await db.execute("UPDATE sources SET status = 'failed' WHERE id = $1", source_id)


async def search(db, collection_id: int, text: str, k: int = 3):
    """Collection-scoped semantic Search → ranked Results.

    WHERE collection_id enforces ADR-0001 isolation; status='ready' enforces the
    "not searchable until ready" gate. Production caches Results in Redis (plumbing).
    """
    (q_vector,) = await embed([text])
    return await db.fetch(
        """SELECT c.content, s.origin, c.embedding <=> $2 AS distance
           FROM chunks c
           JOIN sources s ON s.id = c.source_id
           WHERE c.collection_id = $1 AND s.status = 'ready'
           ORDER BY distance ASC
           LIMIT $3""",
        collection_id, to_pgvector(q_vector), k,
    )


async def ask(db, collection_id: int, question: str, k: int = 3):
    """A Question: Collection-scoped retrieval + LLM synthesis → Answer with Citations."""
    results = await search(db, collection_id, question, k)
    context = "\n".join(
        f"[{i + 1}] (source: {r['origin']}) {r['content']}" for i, r in enumerate(results)
    )
    answer = await complete([
        {"role": "system", "content": (
            "Answer the question using ONLY the numbered context. Cite sources inline "
            "as [1], [2], etc. If the context is insufficient, say so."
        )},
        {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
    ])
    citations = [r["origin"] for r in results]  # production streams this over WebSocket
    return answer, citations


async def open_session(db, collection_id: int) -> int:
    """A Session is a research workspace bound to exactly one Collection."""
    return await db.fetchval("INSERT INTO sessions (collection_id) VALUES ($1) RETURNING id", collection_id)


async def record(db, session_id: int, kind: str, input_text: str, output: str | None = None) -> None:
    """Opt-in activity recording: only happens when a Search/Question names a Session."""
    await db.execute(
        "INSERT INTO session_activity (session_id, kind, input, output) VALUES ($1, $2, $3, $4)",
        session_id, kind, input_text, output,
    )


async def save_brief(db, session_id: int, answer: str, citations: list[str]) -> int:
    """A Brief is a saved Answer (text + Citations) kept within a Session."""
    return await db.fetchval(
        "INSERT INTO briefs (session_id, answer, citations) VALUES ($1, $2, $3) RETURNING id",
        session_id, answer, ", ".join(citations),
    )


# ── Walk the full functional surface, in the order an agent would exercise it ──

async def main() -> None:
    db = await get_db()
    try:
        # ── Setup: schema lives here (DDL in poc.py, not deps.py) ─────────────
        print("Setting up schema...")
        await db.execute("CREATE EXTENSION IF NOT EXISTS vector")

        # Derive the embedding dimension from the model — never hardcode it, so
        # swapping the embedding provider is a zero-code change.
        dim = len((await embed(["dimension probe"]))[0])
        print(f"[Setup] embedding dimension derived from model = {dim}")

        await db.execute("DROP TABLE IF EXISTS briefs, session_activity, sessions, "
                         "chunks, sources, collections CASCADE")
        await db.execute("CREATE TABLE collections (id SERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL)")
        await db.execute(
            """CREATE TABLE sources (
                   id SERIAL PRIMARY KEY,
                   collection_id INT NOT NULL REFERENCES collections(id),
                   origin TEXT NOT NULL,
                   status TEXT NOT NULL DEFAULT 'pending',
                   UNIQUE (collection_id, origin))"""
        )
        await db.execute(
            f"""CREATE TABLE chunks (
                   id SERIAL PRIMARY KEY,
                   source_id INT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                   collection_id INT NOT NULL,
                   content TEXT NOT NULL,
                   embedding vector({dim}) NOT NULL)"""
        )
        await db.execute("CREATE TABLE sessions (id SERIAL PRIMARY KEY, "
                         "collection_id INT NOT NULL REFERENCES collections(id))")
        await db.execute(
            """CREATE TABLE session_activity (
                   id SERIAL PRIMARY KEY,
                   session_id INT NOT NULL REFERENCES sessions(id),
                   kind TEXT NOT NULL,            -- 'search' | 'question'
                   input TEXT NOT NULL,
                   output TEXT)"""
        )
        await db.execute(
            """CREATE TABLE briefs (
                   id SERIAL PRIMARY KEY,
                   session_id INT NOT NULL REFERENCES sessions(id),
                   answer TEXT NOT NULL,
                   citations TEXT NOT NULL)"""
        )

        # 1. Collection — two partitions, to prove isolation later.
        rust = await ensure_collection(db, "rust-lang")
        cooking = await ensure_collection(db, "cooking")
        print(f"\n[Collection] created: rust-lang={rust}, cooking={cooking}")

        # 2. Source ingest — into rust-lang. Status begins 'pending'.
        rust_url = "https://doc.rust-lang.org/book/ch04-00-understanding-ownership.html"
        src = await ingest_source(db, rust, rust_url)
        status = await db.fetchval("SELECT status FROM sources WHERE id = $1", src)
        print(f"[Source] ingested {rust_url} → id={src}, status={status}")

        # 3. GUARANTEE: "not searchable until ready" — search before processing returns nothing.
        before = await search(db, rust, "how is memory freed in Rust?")
        print(f"[Guarantee: status gate] Search while pending → {len(before)} results (expect 0)")

        # 4. Source status lifecycle — simulate the ARQ worker: chunk + embed → ready.
        await process_source(db, src, RUST_OWNERSHIP)
        status = await db.fetchval("SELECT status FROM sources WHERE id = $1", src)
        n_chunks = await db.fetchval("SELECT count(*) FROM chunks WHERE source_id = $1", src)
        print(f"[Lifecycle] processed → status={status}, chunks={n_chunks}  (Chunk + Embedding done)")

        # 5. GUARANTEE: re-ingest refresh-in-place — same origin, same Collection → no duplicate.
        src2 = await ingest_source(db, rust, rust_url)
        await process_source(db, src2, RUST_OWNERSHIP)
        n_after = await db.fetchval("SELECT count(*) FROM chunks WHERE source_id = $1", src2)
        n_sources = await db.fetchval("SELECT count(*) FROM sources WHERE collection_id = $1", rust)
        print(f"[Guarantee: no duplicate] same id={src2 == src}, sources={n_sources}, "
              f"chunks={n_after} (refreshed in place, not doubled)")

        # 6. A second Collection with its own Source — sets up the isolation check.
        cook_src = await ingest_source(db, cooking, "https://example.com/sourdough-basics")
        await process_source(db, cook_src, SOURDOUGH)
        print("[Collection] cooking populated with its own Source")

        # 7. GUARANTEE: a failed Source is excluded from Search.
        bad = await ingest_source(db, rust, "https://example.com/corrupt.pdf")
        await process_source(db, bad, "irrelevant", fail=True)
        bad_status = await db.fetchval("SELECT status FROM sources WHERE id = $1", bad)
        print(f"[Guarantee: failed excluded] corrupt Source status={bad_status} (won't appear in Results)")

        # 8. Collection-scoped Search → ranked Results, with the isolation guarantee.
        question = "How does Rust free memory when a value goes out of scope?"
        results = await search(db, rust, question)
        print(f"\n[Search] scope=rust-lang  {question!r}")
        for r in results:
            print(f"   [{r['distance']:.4f}] ({r['origin']}) {r['content'][:70]}...")
        leaked = any("sourdough" in r["content"].lower() for r in results)
        print(f"[Guarantee: isolation] cooking Chunks in rust-lang Search: {leaked} (expect False)")

        # 9. Question → Answer + Citations (RAG synthesis).
        answer, citations = await ask(db, rust, question)
        print(f"\n[Q&A] Answer:\n   {answer}")
        print(f"[Citations] {citations}")

        # 10. Session — open against rust-lang; record a Search and a Question (opt-in).
        session = await open_session(db, rust)
        await record(db, session, "search", question)
        await record(db, session, "question", question, output=answer)
        await search(db, rust, "what is borrowing?")  # transient: no Session → not recorded
        recorded = await db.fetchval("SELECT count(*) FROM session_activity WHERE session_id = $1", session)
        print(f"\n[Session] id={session}, recorded activities={recorded} (transient Search not among them)")

        # 11. Brief — save the Answer into the Session for later reference.
        brief = await save_brief(db, session, answer, citations)
        n_briefs = await db.fetchval("SELECT count(*) FROM briefs WHERE session_id = $1", session)
        print(f"[Brief] saved id={brief}, briefs in session={n_briefs}")

        print("\nFunctional surface walked end-to-end — fill in FINDINGS.md.")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
