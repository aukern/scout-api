"""Tests for the sessions HTTP layer.

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

from scout_api.sessions.contracts import SessionActivityRow, SessionRow
from scout_api.sessions.errors import SessionCollectionNotFoundError

NOW = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)

SESSION_1 = SessionRow(id=1, collection_id=5, created_at=NOW)
SESSION_2 = SessionRow(id=2, collection_id=5, created_at=NOW)

ACTIVITY_SEARCH = SessionActivityRow(
    id=10, session_id=1, kind="search", query="ml papers", output=None, created_at=NOW
)
ACTIVITY_QUESTION = SessionActivityRow(
    id=11,
    session_id=1,
    kind="question",
    query="What is ML?",
    output="Machine learning is...",
    created_at=NOW,
)


# ---------------------------------------------------------------------------
# POST /sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_session_201(mock_client: AsyncClient) -> None:
    """POST /sessions returns 201 with the session body and Location header."""
    with patch("scout_api.sessions.router.SessionRepository") as MockRepo:
        instance = MockRepo.return_value
        instance.open = AsyncMock(return_value=SESSION_1)

        response = await mock_client.post("/sessions", json={"collection_id": 5})

    assert response.status_code == 201
    data = response.json()
    assert data["id"] == 1
    assert data["collection_id"] == 5
    assert "created_at" in data
    assert "location" in {k.lower() for k in response.headers}
    assert response.headers["location"] == "/sessions/1"


@pytest.mark.asyncio
async def test_open_session_collection_not_found_404(mock_client: AsyncClient) -> None:
    """POST /sessions returns 404 with SES_NF_002 when collection does not exist."""
    with patch("scout_api.sessions.router.SessionRepository") as MockRepo:
        instance = MockRepo.return_value
        instance.open = AsyncMock(side_effect=SessionCollectionNotFoundError(999))

        response = await mock_client.post("/sessions", json={"collection_id": 999})

    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == "SES_NF_002"
    assert "999" in data["error"]["message"]


@pytest.mark.asyncio
async def test_open_session_missing_collection_id_422(mock_client: AsyncClient) -> None:
    """POST /sessions without collection_id returns 422."""
    response = await mock_client.post("/sessions", json={})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_sessions_200(mock_client: AsyncClient) -> None:
    """GET /sessions returns 200 with all sessions."""
    with patch("scout_api.sessions.router.SessionRepository") as MockRepo:
        instance = MockRepo.return_value
        instance.list_all = AsyncMock(return_value=[SESSION_1, SESSION_2])

        response = await mock_client.get("/sessions")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["sessions"]) == 2


@pytest.mark.asyncio
async def test_list_sessions_empty_200(mock_client: AsyncClient) -> None:
    """GET /sessions returns 200 with empty list when no sessions exist."""
    with patch("scout_api.sessions.router.SessionRepository") as MockRepo:
        instance = MockRepo.return_value
        instance.list_all = AsyncMock(return_value=[])

        response = await mock_client.get("/sessions")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["sessions"] == []


@pytest.mark.asyncio
async def test_list_sessions_filtered_by_collection_200(mock_client: AsyncClient) -> None:
    """GET /sessions?collection_id=5 passes the filter and returns matching sessions."""
    with patch("scout_api.sessions.router.SessionRepository") as MockRepo:
        instance = MockRepo.return_value
        instance.list_all = AsyncMock(return_value=[SESSION_1])

        response = await mock_client.get("/sessions?collection_id=5")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    # Verify filter was passed correctly
    call_args = MockRepo.return_value.list_all.call_args
    assert call_args[0][0] == 5 or call_args[1].get("collection_id") == 5


# ---------------------------------------------------------------------------
# GET /sessions/{session_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_session_200(mock_client: AsyncClient) -> None:
    """GET /sessions/{id} returns 200 with session and activity trail."""
    with (
        patch("scout_api.sessions.router.SessionRepository") as MockSessionRepo,
        patch("scout_api.sessions.router.SessionActivityRepository") as MockActivityRepo,
    ):
        MockSessionRepo.return_value.get = AsyncMock(return_value=SESSION_1)
        MockActivityRepo.return_value.list_for_session = AsyncMock(
            return_value=[ACTIVITY_SEARCH, ACTIVITY_QUESTION]
        )

        response = await mock_client.get("/sessions/1")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == 1
    assert data["collection_id"] == 5
    assert len(data["activity"]) == 2
    assert data["activity"][0]["kind"] == "search"
    assert data["activity"][1]["kind"] == "question"
    assert data["activity"][1]["output"] == "Machine learning is..."


@pytest.mark.asyncio
async def test_get_session_empty_activity_200(mock_client: AsyncClient) -> None:
    """GET /sessions/{id} returns 200 with empty activity list for new session."""
    with (
        patch("scout_api.sessions.router.SessionRepository") as MockSessionRepo,
        patch("scout_api.sessions.router.SessionActivityRepository") as MockActivityRepo,
    ):
        MockSessionRepo.return_value.get = AsyncMock(return_value=SESSION_1)
        MockActivityRepo.return_value.list_for_session = AsyncMock(return_value=[])

        response = await mock_client.get("/sessions/1")

    assert response.status_code == 200
    data = response.json()
    assert data["activity"] == []


@pytest.mark.asyncio
async def test_get_session_not_found_404(mock_client: AsyncClient) -> None:
    """GET /sessions/{id} returns 404 with SES_NF_001 when session does not exist."""
    with (
        patch("scout_api.sessions.router.SessionRepository") as MockSessionRepo,
        patch("scout_api.sessions.router.SessionActivityRepository"),
    ):
        MockSessionRepo.return_value.get = AsyncMock(return_value=None)

        response = await mock_client.get("/sessions/999")

    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == "SES_NF_001"
    assert "999" in data["error"]["message"]


# ---------------------------------------------------------------------------
# DELETE /sessions/{session_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_session_204(mock_client: AsyncClient) -> None:
    """DELETE /sessions/{id} returns 204 with no body when session exists."""
    with patch("scout_api.sessions.router.SessionRepository") as MockRepo:
        instance = MockRepo.return_value
        instance.delete = AsyncMock(return_value=True)

        response = await mock_client.delete("/sessions/1")

    assert response.status_code == 204
    assert response.content == b""


@pytest.mark.asyncio
async def test_delete_session_not_found_404(mock_client: AsyncClient) -> None:
    """DELETE /sessions/{id} returns 404 with SES_NF_001 when session does not exist."""
    with patch("scout_api.sessions.router.SessionRepository") as MockRepo:
        instance = MockRepo.return_value
        instance.delete = AsyncMock(return_value=False)

        response = await mock_client.delete("/sessions/999")

    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == "SES_NF_001"
    assert "999" in data["error"]["message"]
