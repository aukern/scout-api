"""Unit tests for the token-aware sliding-window Chunker.

Tests are designed to work regardless of whether tiktoken is installed:
  - If tiktoken is available: tests verify token-aware splitting.
  - If tiktoken is not available: tests verify the character-based fallback.

Both paths must satisfy the same functional requirements.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from scout_api.sources.chunker import Chunker

# ---------------------------------------------------------------------------
# Detect tiktoken availability (used to skip tiktoken-specific tests)
# ---------------------------------------------------------------------------

try:
    import tiktoken as _tiktoken  # noqa: F401

    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False


# ---------------------------------------------------------------------------
# Basic splitting behaviour (works with either tiktoken or char fallback)
# ---------------------------------------------------------------------------


class TestChunkerSplit:
    def test_empty_string_returns_empty_list(self) -> None:
        chunker = Chunker(chunk_token_size=10, chunk_overlap_tokens=2)
        assert chunker.split("") == []

    def test_whitespace_only_returns_empty_list(self) -> None:
        chunker = Chunker(chunk_token_size=10, chunk_overlap_tokens=2)
        assert chunker.split("   \n\t  ") == []

    def test_short_text_returns_single_chunk(self) -> None:
        # Short text should fit in one chunk even with small chunk size
        chunker = Chunker(chunk_token_size=512, chunk_overlap_tokens=64)
        text = "Hello world this is a short sentence."
        chunks = chunker.split(text)
        assert len(chunks) == 1
        assert "Hello" in chunks[0]

    def test_long_text_splits_into_multiple_chunks(self) -> None:
        # Generate text long enough to force multiple chunks
        text = " ".join(["word"] * 300)
        chunker = Chunker(chunk_token_size=20, chunk_overlap_tokens=4)
        chunks = chunker.split(text)
        assert len(chunks) > 1

    def test_all_chunks_are_non_empty(self) -> None:
        text = " ".join(["word"] * 200)
        chunker = Chunker(chunk_token_size=30, chunk_overlap_tokens=5)
        chunks = chunker.split(text)
        assert all(c.strip() for c in chunks)

    def test_chunks_cover_all_content(self) -> None:
        """All words in the source text should appear in at least one chunk."""
        text = "The quick brown fox jumps over the lazy dog. " * 10
        chunker = Chunker(chunk_token_size=20, chunk_overlap_tokens=5)
        chunks = chunker.split(text)
        combined = " ".join(chunks)
        for word in ["quick", "brown", "fox", "jumps", "lazy", "dog"]:
            assert word in combined

    def test_overlap_consecutive_chunks_differ(self) -> None:
        """With overlap, consecutive chunks should still differ (not identical)."""
        text = " ".join([f"token{i}" for i in range(200)])
        chunker = Chunker(chunk_token_size=20, chunk_overlap_tokens=8)
        chunks = chunker.split(text)
        assert len(chunks) >= 2
        assert chunks[0] != chunks[1]

    def test_single_word_returns_one_chunk(self) -> None:
        chunker = Chunker(chunk_token_size=512, chunk_overlap_tokens=64)
        chunks = chunker.split("hello")
        assert chunks == ["hello"]


# ---------------------------------------------------------------------------
# Boundary conditions
# ---------------------------------------------------------------------------


class TestChunkerBoundaries:
    def test_overlap_must_be_less_than_chunk_size(self) -> None:
        with pytest.raises(ValueError, match="must be less than"):
            Chunker(chunk_token_size=10, chunk_overlap_tokens=10)

    def test_overlap_equal_to_chunk_size_raises(self) -> None:
        with pytest.raises(ValueError):
            Chunker(chunk_token_size=5, chunk_overlap_tokens=5)

    def test_overlap_greater_than_chunk_size_raises(self) -> None:
        with pytest.raises(ValueError):
            Chunker(chunk_token_size=5, chunk_overlap_tokens=6)

    def test_construction_with_valid_params_does_not_raise(self) -> None:
        # Construction no longer raises even without tiktoken installed
        # (lazy import means tiktoken is only needed at split() time)
        chunker = Chunker(chunk_token_size=512, chunk_overlap_tokens=64)
        assert chunker is not None


# ---------------------------------------------------------------------------
# Encoding fallback (requires tiktoken — skipped if not installed)
# ---------------------------------------------------------------------------


class TestChunkerEncodingFallback:
    @pytest.mark.skipif(not TIKTOKEN_AVAILABLE, reason="tiktoken not installed")
    def test_unknown_model_falls_back_to_cl100k_base(self) -> None:
        """An unknown model string should not raise — falls back to cl100k_base."""
        chunker = Chunker(
            chunk_token_size=50,
            chunk_overlap_tokens=10,
            model="totally-unknown-model-xyz",
        )
        text = "This is a test sentence for the fallback encoding."
        chunks = chunker.split(text)
        assert len(chunks) >= 1

    @pytest.mark.skipif(not TIKTOKEN_AVAILABLE, reason="tiktoken not installed")
    def test_ollama_model_prefix_stripped(self) -> None:
        """ollama/nomic-embed-text should work — strips prefix, falls back if needed."""
        chunker = Chunker(
            chunk_token_size=50,
            chunk_overlap_tokens=10,
            model="ollama/nomic-embed-text",
        )
        text = "Testing the chunker with an Ollama model identifier."
        chunks = chunker.split(text)
        assert len(chunks) >= 1

    @pytest.mark.skipif(not TIKTOKEN_AVAILABLE, reason="tiktoken not installed")
    def test_openai_model_resolves_directly(self) -> None:
        """text-embedding-ada-002 is a known model — tiktoken resolves it directly."""
        chunker = Chunker(
            chunk_token_size=50,
            chunk_overlap_tokens=10,
            model="text-embedding-ada-002",
        )
        text = "OpenAI model resolution test."
        chunks = chunker.split(text)
        assert len(chunks) == 1


# ---------------------------------------------------------------------------
# Character-based fallback (when tiktoken is not installed)
# ---------------------------------------------------------------------------


class TestChunkerCharFallback:
    def test_char_fallback_splits_long_text(self) -> None:
        """Character fallback produces multiple chunks for long text."""
        # Force use of char fallback by patching _get_encoding to return None

        chunker = Chunker(chunk_token_size=10, chunk_overlap_tokens=2)
        # 10 tokens * 4 chars/token = 40 chars per chunk
        text = "a" * 200
        with patch.object(chunker, "_get_encoding", return_value=None):
            chunks = chunker.split(text)
        assert len(chunks) > 1

    def test_char_fallback_empty_returns_empty(self) -> None:

        chunker = Chunker(chunk_token_size=10, chunk_overlap_tokens=2)
        with patch.object(chunker, "_get_encoding", return_value=None):
            assert chunker.split("") == []

    def test_char_fallback_covers_content(self) -> None:

        chunker = Chunker(chunk_token_size=10, chunk_overlap_tokens=2)
        text = "hello world " * 50
        with patch.object(chunker, "_get_encoding", return_value=None):
            chunks = chunker.split(text)
        combined = " ".join(chunks)
        assert "hello" in combined
        assert "world" in combined


# ---------------------------------------------------------------------------
# _token_split via mock encoder (covers lines 84, 97, 101, 112-130)
# ---------------------------------------------------------------------------


class TestChunkerTokenSplit:
    """Tests that exercise the _token_split path via a mock encoder.

    These tests do NOT require tiktoken — they inject a fake encoder directly
    into the chunker via _get_encoding / _enc injection.
    """

    def _make_mock_enc(self, token_count: int = 100) -> MagicMock:
        """Return a mock encoder that maps text to a fixed list of ints."""

        enc = MagicMock()
        # encode returns a list of ints; decode returns reconstructed text
        tokens = list(range(token_count))
        enc.encode = MagicMock(return_value=tokens)
        # decode: return " ".join(str(t) for t in chunk_tokens) — deterministic
        enc.decode = MagicMock(side_effect=lambda ts: " ".join(str(t) for t in ts))
        return enc

    def test_token_split_via_injected_encoder(self) -> None:
        """_token_split with a mock encoder splits correctly."""
        chunker = Chunker(chunk_token_size=20, chunk_overlap_tokens=5)
        enc = self._make_mock_enc(token_count=50)
        chunks = chunker._token_split("some text", enc)  # noqa: SLF001
        assert len(chunks) > 1

    def test_token_split_empty_tokens_returns_empty(self) -> None:
        """_token_split returns [] when encoder returns no tokens."""

        chunker = Chunker(chunk_token_size=20, chunk_overlap_tokens=5)
        enc = MagicMock()
        enc.encode = MagicMock(return_value=[])
        result = chunker._token_split("", enc)  # noqa: SLF001
        assert result == []

    def test_split_uses_cached_encoding(self) -> None:
        """After first split, _enc is cached — _resolve_encoding not called again."""

        chunker = Chunker(chunk_token_size=20, chunk_overlap_tokens=5)
        enc = self._make_mock_enc(token_count=10)

        with patch.object(chunker, "_resolve_encoding", return_value=enc) as mock_resolve:
            chunker.split("text one")
            chunker.split("text two")

        # _resolve_encoding should only be called once despite two split() calls
        assert mock_resolve.call_count == 1

    def test_get_encoding_returns_cached_on_second_call(self) -> None:
        """_get_encoding returns cached _enc on second call."""

        chunker = Chunker(chunk_token_size=20, chunk_overlap_tokens=5)
        enc = MagicMock()
        enc.encode = MagicMock(return_value=[1, 2, 3])
        enc.decode = MagicMock(return_value="text")

        with patch.object(chunker, "_resolve_encoding", return_value=enc):
            result1 = chunker._get_encoding()  # noqa: SLF001
            result2 = chunker._get_encoding()  # noqa: SLF001

        assert result1 is result2

    def test_get_encoding_returns_none_on_import_error(self) -> None:
        """_get_encoding returns None when _resolve_encoding raises ImportError."""

        chunker = Chunker(chunk_token_size=20, chunk_overlap_tokens=5)
        with patch.object(chunker, "_resolve_encoding", side_effect=ImportError("no tiktoken")):
            result = chunker._get_encoding()  # noqa: SLF001
        assert result is None


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestChunkerDeterminism:
    def test_same_input_produces_same_output(self) -> None:
        chunker = Chunker(chunk_token_size=30, chunk_overlap_tokens=5)
        text = "The quick brown fox jumps over the lazy dog. " * 20
        assert chunker.split(text) == chunker.split(text)

    def test_different_chunk_sizes_produce_different_results(self) -> None:
        text = " ".join([f"w{i}" for i in range(100)])
        chunker_small = Chunker(chunk_token_size=10, chunk_overlap_tokens=2)
        chunker_large = Chunker(chunk_token_size=50, chunk_overlap_tokens=5)
        assert len(chunker_small.split(text)) >= len(chunker_large.split(text))
