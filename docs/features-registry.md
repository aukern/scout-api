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
