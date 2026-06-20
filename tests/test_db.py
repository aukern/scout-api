"""Tests for the db module (pool accessor)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import Request

from scout_api.db import get_pool


def _make_request(pool: object | None = None) -> Request:
    """Create a minimal Request-like object with app.state.pool."""
    app = MagicMock()
    app.state = MagicMock()
    app.state.pool = pool
    request = MagicMock(spec=Request)
    request.app = app
    return request


def test_get_pool_returns_pool_from_app_state() -> None:
    """get_pool returns the pool stored on app.state.pool."""
    mock_pool = MagicMock()
    request = _make_request(pool=mock_pool)
    result = get_pool(request)
    assert result is mock_pool


def test_get_pool_raises_when_pool_is_none() -> None:
    """get_pool raises RuntimeError if the pool was not initialized."""
    request = _make_request(pool=None)
    with pytest.raises(RuntimeError, match="Database pool not initialized"):
        get_pool(request)
