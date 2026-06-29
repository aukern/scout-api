"""Unit tests for the sources MCP module.

Tests the module-level functions and importability without fastmcp installed.
The tool logic is exercised via the service layer (test_service.py).
"""

from __future__ import annotations

import sys

import pytest


class TestMcpModuleImport:
    def test_mcp_module_importable(self) -> None:
        """The sources.mcp module can be imported without fastmcp installed."""
        from scout_api.sources import mcp as mcp_module  # noqa: F401

        assert hasattr(mcp_module, "create_mcp_app")
        assert hasattr(mcp_module, "_build_mcp_server")

    def test_create_mcp_app_raises_import_error_without_fastmcp(self) -> None:
        """create_mcp_app raises ImportError when fastmcp is not installed."""
        from unittest.mock import patch

        with patch.dict(sys.modules, {"fastmcp": None, "mcp": None, "mcp.types": None}):
            from scout_api.sources.mcp import create_mcp_app

            with pytest.raises((ImportError, TypeError)):
                create_mcp_app()

    def test_sources_mcp_module_has_logger(self) -> None:
        """The sources MCP module sets up a structlog logger at module level."""
        from scout_api.sources import mcp as mcp_module

        assert hasattr(mcp_module, "logger")


class TestMcpStructure:
    def test_errors_are_importable_from_sources(self) -> None:
        """Sources error classes are importable (used in MCP ToolError wrapping)."""
        from scout_api.sources.errors import (
            CollectionNotFoundError,
            InvalidOriginError,
            SourceIngestionError,
        )

        assert CollectionNotFoundError(1).code == "SRC_NF_001"
        assert InvalidOriginError("bad").code == "SRC_VAL_001"
        assert SourceIngestionError("boom").code == "SRC_ING_001"

    def test_sources_contracts_importable(self) -> None:
        """Sources contracts are importable — required by the MCP tool body."""
        import datetime

        from scout_api.sources.contracts import SourceRow, SourceStatus

        row = SourceRow(
            id=1,
            collection_id=2,
            origin="https://example.com",
            status=SourceStatus.PENDING,
            created_at=datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC),
            updated_at=datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC),
        )
        assert row.status == SourceStatus.PENDING
        assert row.status.value == "pending"

    def test_create_mcp_app_is_callable(self) -> None:
        """create_mcp_app is a callable (guard against accidental module-level call)."""
        from scout_api.sources.mcp import create_mcp_app

        assert callable(create_mcp_app)

    def test_build_mcp_server_is_callable(self) -> None:
        """_build_mcp_server is a callable (guard against accidental module-level call)."""
        from scout_api.sources.mcp import _build_mcp_server

        assert callable(_build_mcp_server)

    def test_adapters_importable_for_mcp(self) -> None:
        """Storage and queue adapters are importable (used in MCP tool bodies)."""
        from scout_api.sources.queue import InMemoryQueueAdapter
        from scout_api.sources.storage import InMemoryStorageAdapter

        storage = InMemoryStorageAdapter()
        queue = InMemoryQueueAdapter()

        assert isinstance(storage.store, dict)
        assert isinstance(queue.jobs, list)

    def test_ingest_service_importable_for_mcp(self) -> None:
        """IngestService is importable — required by the MCP tool body."""
        from scout_api.sources.service import IngestService

        assert callable(IngestService)
