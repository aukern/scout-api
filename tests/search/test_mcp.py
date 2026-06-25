"""Unit tests for the search MCP module.

Tests the module-level functions that don't require fastmcp/mcp installed.
The tool logic is tested via the service layer (test_service.py).
"""

from __future__ import annotations

import pytest


class TestMcpModuleImport:
    def test_mcp_module_importable(self) -> None:
        """The mcp module can be imported without fastmcp installed."""
        from scout_api.search import mcp as mcp_module  # noqa: F401

        assert hasattr(mcp_module, "create_mcp_app")
        assert hasattr(mcp_module, "_build_mcp_server")

    def test_create_mcp_app_raises_import_error_without_fastmcp(self) -> None:
        """create_mcp_app raises ImportError when fastmcp is not installed."""
        import sys
        from unittest.mock import patch  # noqa: F401

        # Simulate fastmcp not being installed
        with patch.dict(sys.modules, {"fastmcp": None, "mcp": None, "mcp.types": None}):
            from scout_api.search.mcp import create_mcp_app

            with pytest.raises((ImportError, TypeError)):
                create_mcp_app()

    def test_make_cache_key_used_in_mcp(self) -> None:
        """The cache key function is accessible from the search package."""
        from scout_api.search.cache import make_cache_key

        key = make_cache_key(collection_id=3, query_text="hello")
        assert "3" in key
        assert key.startswith("search:")
