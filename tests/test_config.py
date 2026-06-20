"""Tests for the config module."""

from __future__ import annotations

import os
from unittest.mock import patch

from scout_api.config import Settings, get_settings


def test_settings_defaults() -> None:
    """Settings provides sensible defaults when no env vars are set."""
    with patch.dict(os.environ, {}, clear=False):
        s = Settings()
    assert s.app_port == 8000
    assert s.log_level == "INFO"
    assert s.max_connections == 10


def test_settings_database_url_from_env() -> None:
    """DATABASE_URL env var overrides the default."""
    env = {"DATABASE_URL": "postgresql://test:pass@localhost:5432/testdb"}
    with patch.dict(os.environ, env, clear=False):
        s = Settings()
    assert s.database_url == "postgresql://test:pass@localhost:5432/testdb"


def test_get_settings_returns_settings_instance() -> None:
    """get_settings() returns a Settings instance."""
    s = get_settings()
    assert isinstance(s, Settings)
