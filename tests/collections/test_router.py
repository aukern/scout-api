"""Integration tests for the collections HTTP layer.

These tests cover the full HTTP request/response cycle using:
1. mock_client: no real database required — tests routing, status codes, and
   error envelope structure using AsyncMock.
2. async_client: real test database — tests end-to-end behavior including
   uniqueness constraints and cascade (marked integration, skipped without DB).

The mock-based tests are the primary CI tests. The integration tests run
when TEST_DATABASE_URL is set (local dev with Docker or CI with Postgres service).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from scout_api.collections.errors import (
    CollectionAlreadyExistsError,
    CollectionNotFoundError,
)
from scout_api.collections.repository import CollectionRow


# ---------------------------------------------------------------------------
# POST /collections
# ---------------------------------------------------------------------------


async def test_create_collection_returns_201(mock_client: AsyncClient) -> None:
    """POST /collections with a valid name returns 201 and the created collection."""
    with patch(
        "scout_api.collections.router.CollectionRepository"
    ) as MockRepo:
        instance = MockRepo.return_value
        instance.create = AsyncMock(return_value=CollectionRow(id=1, name="my-research"))

        response = await mock_client.post(
            "/collections", json={"name": "my-research"}
        )

    assert response.status_code == 201
    data = response.json()
    assert data["id"] == 1
    assert data["name"] == "my-research"
    assert "location" in {k.lower() for k in response.headers}


async def test_create_collection_returns_409_on_duplicate(
    mock_client: AsyncClient,
) -> None:
    """POST /collections with a duplicate name returns 409 with error envelope."""
    with patch(
        "scout_api.collections.router.CollectionRepository"
    ) as MockRepo:
        instance = MockRepo.return_value
        instance.create = AsyncMock(
            side_effect=CollectionAlreadyExistsError("duplicate")
        )

        response = await mock_client.post(
            "/collections", json={"name": "duplicate"}
        )

    assert response.status_code == 409
    data = response.json()
    assert data["error"]["code"] == "COLLECTION_ALREADY_EXISTS"
    assert "duplicate" in data["error"]["message"]


async def test_create_collection_validates_name_characters(
    mock_client: AsyncClient,
) -> None:
    """POST /collections rejects names with invalid characters."""
    response = await mock_client.post(
        "/collections", json={"name": "bad name!"}
    )
    assert response.status_code == 422


async def test_create_collection_rejects_blank_name(
    mock_client: AsyncClient,
) -> None:
    """POST /collections rejects blank names."""
    response = await mock_client.post("/collections", json={"name": "   "})
    assert response.status_code == 422


async def test_create_collection_rejects_empty_name(
    mock_client: AsyncClient,
) -> None:
    """POST /collections rejects empty string names."""
    response = await mock_client.post("/collections", json={"name": ""})
    assert response.status_code == 422


async def test_create_collection_rejects_missing_name(
    mock_client: AsyncClient,
) -> None:
    """POST /collections with no 'name' field returns 422."""
    response = await mock_client.post("/collections", json={})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /collections
# ---------------------------------------------------------------------------


async def test_list_collections_returns_200_empty(mock_client: AsyncClient) -> None:
    """GET /collections on an empty system returns 200 with an empty list."""
    with patch(
        "scout_api.collections.router.CollectionRepository"
    ) as MockRepo:
        instance = MockRepo.return_value
        instance.list_all = AsyncMock(return_value=[])

        response = await mock_client.get("/collections")

    assert response.status_code == 200
    data = response.json()
    assert data["collections"] == []
    assert data["total"] == 0


async def test_list_collections_returns_all(mock_client: AsyncClient) -> None:
    """GET /collections returns all collections with id and name."""
    rows = [
        CollectionRow(id=1, name="alpha"),
        CollectionRow(id=2, name="beta"),
    ]
    with patch(
        "scout_api.collections.router.CollectionRepository"
    ) as MockRepo:
        instance = MockRepo.return_value
        instance.list_all = AsyncMock(return_value=rows)

        response = await mock_client.get("/collections")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    names = [c["name"] for c in data["collections"]]
    assert "alpha" in names
    assert "beta" in names


# ---------------------------------------------------------------------------
# DELETE /collections/{name}
# ---------------------------------------------------------------------------


async def test_delete_collection_returns_204(mock_client: AsyncClient) -> None:
    """DELETE /collections/{name} returns 204 with no body on success."""
    with patch(
        "scout_api.collections.router.CollectionRepository"
    ) as MockRepo:
        instance = MockRepo.return_value
        instance.delete = AsyncMock(return_value=None)

        response = await mock_client.delete("/collections/my-research")

    assert response.status_code == 204
    assert response.content == b""


async def test_delete_collection_returns_404_when_not_found(
    mock_client: AsyncClient,
) -> None:
    """DELETE /collections/{name} returns 404 if the collection does not exist."""
    with patch(
        "scout_api.collections.router.CollectionRepository"
    ) as MockRepo:
        instance = MockRepo.return_value
        instance.delete = AsyncMock(
            side_effect=CollectionNotFoundError("ghost")
        )

        response = await mock_client.delete("/collections/ghost")

    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == "COLLECTION_NOT_FOUND"
    assert "ghost" in data["error"]["message"]


# ---------------------------------------------------------------------------
# Glossary compliance
# ---------------------------------------------------------------------------


async def test_response_uses_glossary_vocabulary(mock_client: AsyncClient) -> None:
    """Verify response fields use 'collection'/'collections', not forbidden terms."""
    with patch(
        "scout_api.collections.router.CollectionRepository"
    ) as MockRepo:
        instance = MockRepo.return_value
        instance.list_all = AsyncMock(
            return_value=[CollectionRow(id=1, name="alpha")]
        )

        response = await mock_client.get("/collections")

    body_text = response.text.lower()
    # Must use glossary vocabulary
    assert "collection" in body_text
    # Must NOT use forbidden terms
    forbidden = ["namespace", "tenant", "index", "corpus"]
    for term in forbidden:
        assert term not in body_text, f"Response contains forbidden term: {term}"


# ---------------------------------------------------------------------------
# Integration tests (require TEST_DATABASE_URL)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_full_crud_lifecycle(async_client: AsyncClient) -> None:
    """End-to-end: create, list, delete a collection against a real DB."""
    # Create
    create_resp = await async_client.post(
        "/collections", json={"name": "e2e-test"}
    )
    assert create_resp.status_code == 201
    assert create_resp.json()["name"] == "e2e-test"

    # List — should appear
    list_resp = await async_client.get("/collections")
    assert list_resp.status_code == 200
    names = [c["name"] for c in list_resp.json()["collections"]]
    assert "e2e-test" in names

    # Delete
    del_resp = await async_client.delete("/collections/e2e-test")
    assert del_resp.status_code == 204

    # List again — should be gone
    list_resp2 = await async_client.get("/collections")
    names2 = [c["name"] for c in list_resp2.json()["collections"]]
    assert "e2e-test" not in names2


@pytest.mark.integration
async def test_duplicate_creation_rejected_e2e(async_client: AsyncClient) -> None:
    """Creating two collections with the same name returns 409."""
    await async_client.post("/collections", json={"name": "dup-e2e"})
    second = await async_client.post("/collections", json={"name": "dup-e2e"})
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "COLLECTION_ALREADY_EXISTS"


@pytest.mark.integration
async def test_delete_nonexistent_returns_404_e2e(async_client: AsyncClient) -> None:
    """Deleting a collection that does not exist returns 404."""
    resp = await async_client.delete("/collections/nonexistent-e2e-xyz")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "COLLECTION_NOT_FOUND"
