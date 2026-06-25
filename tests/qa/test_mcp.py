"""Unit tests for the QA MCP module.

Tests the module-level structure and importability without fastmcp installed.
The tool logic is exercised via service-layer tests (test_service.py).
"""

from __future__ import annotations

import sys

import pytest


class TestMcpModuleImport:
    def test_mcp_module_importable(self) -> None:
        """The qa.mcp module can be imported without fastmcp installed."""
        from scout_api.qa import mcp as mcp_module  # noqa: F401

        assert hasattr(mcp_module, "create_mcp_app")
        assert hasattr(mcp_module, "_build_mcp_server")

    def test_create_mcp_app_raises_import_error_without_fastmcp(self) -> None:
        """create_mcp_app raises ImportError when fastmcp is not installed."""
        from unittest.mock import patch

        with patch.dict(sys.modules, {"fastmcp": None, "mcp": None, "mcp.types": None}):
            from scout_api.qa.mcp import create_mcp_app

            with pytest.raises((ImportError, TypeError)):
                create_mcp_app()

    def test_qa_mcp_module_has_logger(self) -> None:
        """The QA MCP module sets up a structlog logger at module level."""
        from scout_api.qa import mcp as mcp_module

        assert hasattr(mcp_module, "logger")


class TestMcpStructure:
    def test_errors_are_importable_from_qa(self) -> None:
        """QA error classes are importable (used in MCP ToolError wrapping)."""
        from scout_api.qa.errors import (
            QACollectionNotFoundError,
            QANoContextError,
            QASynthesisError,
            QAValidationError,
        )

        assert QACollectionNotFoundError(1).code == "QA_COL_001"
        assert QANoContextError(1).code == "QA_CTX_001"
        assert QASynthesisError("x").code == "QA_SYN_001"
        assert QAValidationError("x").code == "QA_VAL_001"

    def test_qa_contracts_importable(self) -> None:
        """QA contracts are importable — required by the MCP tool body."""
        from scout_api.qa.contracts import AnswerChunk, Citation, Question

        q = Question(collection_id=1, text="test?")
        assert q.collection_id == 1
        c = Citation(
            source_id=1,
            source_origin="https://example.com",
            chunk_ids=[1],
            inline_marker="[1]",
        )
        assert c.source_id == 1
        a = AnswerChunk(text="hello", is_final=False)
        assert a.citations == []

    def test_create_mcp_app_is_callable(self) -> None:
        """create_mcp_app is a callable (guard against accidental module-level call)."""
        from scout_api.qa.mcp import create_mcp_app

        assert callable(create_mcp_app)
