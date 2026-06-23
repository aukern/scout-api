"""Unit tests for processing-specific error classes.

Covers SRC_PROC_001, SRC_PROC_002, SRC_PROC_003.
"""

from __future__ import annotations

from scout_api.sources.errors import (
    EmbeddingDimensionMismatchError,
    SourceNotFoundError,
    SourceProcessingError,
)


class TestSourceProcessingError:
    def test_has_correct_code(self) -> None:
        err = SourceProcessingError(source_id=1, detail="network error")
        assert err.code == "SRC_PROC_001"

    def test_message_includes_source_id(self) -> None:
        err = SourceProcessingError(source_id=42, detail="timeout")
        assert "42" in err.message

    def test_message_includes_detail(self) -> None:
        err = SourceProcessingError(source_id=1, detail="something bad")
        assert "something bad" in err.message

    def test_status_code_is_500(self) -> None:
        err = SourceProcessingError(source_id=1, detail="error")
        assert err.status_code == 500


class TestEmbeddingDimensionMismatchError:
    def test_has_correct_code(self) -> None:
        err = EmbeddingDimensionMismatchError(probe_dim=768, schema_dim=1536)
        assert err.code == "SRC_PROC_002"

    def test_message_includes_both_dimensions(self) -> None:
        err = EmbeddingDimensionMismatchError(probe_dim=768, schema_dim=1536)
        assert "768" in err.message
        assert "1536" in err.message

    def test_status_code_is_500(self) -> None:
        err = EmbeddingDimensionMismatchError(probe_dim=1, schema_dim=2)
        assert err.status_code == 500


class TestSourceNotFoundError:
    def test_has_correct_code(self) -> None:
        err = SourceNotFoundError(source_id=99)
        assert err.code == "SRC_PROC_003"

    def test_message_includes_source_id(self) -> None:
        err = SourceNotFoundError(source_id=99)
        assert "99" in err.message

    def test_status_code_is_404(self) -> None:
        err = SourceNotFoundError(source_id=1)
        assert err.status_code == 404
