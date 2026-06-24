"""Tests for the briefs HTTP layer.

Mock-based tests (no real DB required) cover the full request/response cycle:
- Status codes
- Response body shape
- Error envelope format
- Location headers

Integration tests (marked @pytest.mark.integration) require TEST_DATABASE_URL.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from scout_api.briefs.contracts import BriefCitation, BriefRow
from scout_api.briefs.errors import BriefSessionNotFoundError

NOW = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)

CITATION_FULL = BriefCitation(source_id=3, chunk_id=41, excerpt="The key insight is here.")
CITATION_SOURCE_ONLY = BriefCitation(source_id=5)

BRIEF_1 = BriefRow(
    id=1,
    session_id=7,
    answer_text="Based on the papers, the key insight is...",
    citations=[CITATION_FULL, CITATION_SOURCE_ONLY],
    created_at=NOW,
)

BRIEF_EMPTY_CITATIONS = BriefRow(
    id=2,
    session_id=7,
    answer_text="A brief with no citations.",
    citations=[],
    created_at=NOW,
)


# ---------------------------------------------------------------------------
# POST /sessions/{session_id}/briefs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_brief_201(mock_client: AsyncClient) -> None:
    """POST /sessions/{session_id}/briefs returns 201 with body and Location header."""
    with patch("scout_api.briefs.router.BriefRepository") as MockRepo:
        instance = MockRepo.return_value
        instance.save = AsyncMock(return_value=BRIEF_1)

        response = await mock_client.post(
            "/sessions/7/briefs",
            json={
                "answer_text": "Based on the papers, the key insight is...",
                "citations": [
                    {"source_id": 3, "chunk_id": 41, "excerpt": "The key insight is here."},
                    {"source_id": 5},
                ],
            },
        )

    assert response.status_code == 201
    data = response.json()
    assert data["id"] == 1
    assert data["session_id"] == 7
    assert data["answer_text"] == "Based on the papers, the key insight is..."
    assert len(data["citations"]) == 2
    assert data["citations"][0]["source_id"] == 3
    assert data["citations"][0]["chunk_id"] == 41
    assert data["citations"][0]["excerpt"] == "The key insight is here."
    assert data["citations"][1]["source_id"] == 5
    assert data["citations"][1]["chunk_id"] is None
    assert "created_at" in data
    assert "location" in {k.lower() for k in response.headers}
    assert response.headers["location"] == "/sessions/7/briefs/1"


@pytest.mark.asyncio
async def test_save_brief_session_not_found_404(mock_client: AsyncClient) -> None:
    """POST /sessions/{session_id}/briefs returns 404 with BRF_NF_001 when session missing."""
    with patch("scout_api.briefs.router.BriefRepository") as MockRepo:
        instance = MockRepo.return_value
        instance.save = AsyncMock(side_effect=BriefSessionNotFoundError(999))

        response = await mock_client.post(
            "/sessions/999/briefs",
            json={"answer_text": "Any answer."},
        )

    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == "BRF_NF_001"
    assert "999" in data["error"]["message"]


@pytest.mark.asyncio
async def test_save_brief_missing_answer_text_422(mock_client: AsyncClient) -> None:
    """POST /sessions/{session_id}/briefs returns 422 when answer_text is missing."""
    response = await mock_client.post(
        "/sessions/7/briefs",
        json={"citations": []},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_save_brief_empty_answer_text_422(mock_client: AsyncClient) -> None:
    """POST /sessions/{session_id}/briefs returns 422 when answer_text is empty string."""
    response = await mock_client.post(
        "/sessions/7/briefs",
        json={"answer_text": ""},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_save_brief_empty_citations_201(mock_client: AsyncClient) -> None:
    """POST /sessions/{session_id}/briefs succeeds when citations list is omitted."""
    with patch("scout_api.briefs.router.BriefRepository") as MockRepo:
        instance = MockRepo.return_value
        instance.save = AsyncMock(return_value=BRIEF_EMPTY_CITATIONS)

        response = await mock_client.post(
            "/sessions/7/briefs",
            json={"answer_text": "A brief with no citations."},
        )

    assert response.status_code == 201
    data = response.json()
    assert data["citations"] == []


# ---------------------------------------------------------------------------
# GET /sessions/{session_id}/briefs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_briefs_200(mock_client: AsyncClient) -> None:
    """GET /sessions/{session_id}/briefs returns 200 with correct body shape."""
    with patch("scout_api.briefs.router.BriefRepository") as MockRepo:
        instance = MockRepo.return_value
        instance.list_for_session = AsyncMock(return_value=[BRIEF_1])

        response = await mock_client.get("/sessions/7/briefs")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["briefs"]) == 1
    brief = data["briefs"][0]
    assert brief["id"] == 1
    assert brief["session_id"] == 7
    assert brief["answer_text"] == "Based on the papers, the key insight is..."
    assert len(brief["citations"]) == 2
    assert "created_at" in brief


@pytest.mark.asyncio
async def test_list_briefs_empty_200(mock_client: AsyncClient) -> None:
    """GET /sessions/{session_id}/briefs returns 200 with empty list when no Briefs saved."""
    with patch("scout_api.briefs.router.BriefRepository") as MockRepo:
        instance = MockRepo.return_value
        instance.list_for_session = AsyncMock(return_value=[])

        response = await mock_client.get("/sessions/7/briefs")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["briefs"] == []


@pytest.mark.asyncio
async def test_list_briefs_session_not_found_404(mock_client: AsyncClient) -> None:
    """GET /sessions/{session_id}/briefs returns 404 with BRF_NF_001 when session missing."""
    with patch("scout_api.briefs.router.BriefRepository") as MockRepo:
        instance = MockRepo.return_value
        instance.list_for_session = AsyncMock(side_effect=BriefSessionNotFoundError(999))

        response = await mock_client.get("/sessions/999/briefs")

    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == "BRF_NF_001"
    assert "999" in data["error"]["message"]
