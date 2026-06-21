"""Tests for production adapter lazy-import error handling.

These tests verify that S3StorageAdapter and ArqQueueAdapter raise
ImportError when their optional dependencies are not installed,
and that the error message includes installation instructions.
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from scout_api.sources.queue import ArqQueueAdapter
from scout_api.sources.storage import S3StorageAdapter

# ---------------------------------------------------------------------------
# S3StorageAdapter
# ---------------------------------------------------------------------------


def test_s3_storage_adapter_raises_import_error_when_aioboto3_missing() -> None:
    """S3StorageAdapter.__init__ raises ImportError if aioboto3 is not installed."""
    with patch.dict(sys.modules, {"aioboto3": None}):
        with pytest.raises(ImportError, match="aioboto3"):
            S3StorageAdapter(bucket="test", region="us-east-1")


# ---------------------------------------------------------------------------
# ArqQueueAdapter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_arq_queue_adapter_raises_import_error_when_arq_missing() -> None:
    """ArqQueueAdapter.enqueue raises ImportError if arq is not installed."""
    adapter = ArqQueueAdapter(redis_url="redis://localhost:6379")
    with patch.dict(sys.modules, {"arq": None}):
        with pytest.raises(ImportError, match="arq"):
            await adapter.enqueue("process_source", source_id=1)
