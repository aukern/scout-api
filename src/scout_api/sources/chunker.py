"""Token-aware sliding-window chunker for source content.

Splits raw text into overlapping chunks of a fixed token size using tiktoken.
If tiktoken cannot find an encoding for the configured model, falls back to
the ``cl100k_base`` encoding (OpenAI's default — adequate for any natural-
language embedding model).

If tiktoken is not installed, falls back to a character-based splitter that
approximates token boundaries (1 token ≈ 4 characters). This fallback is
logged clearly and is acceptable for prototypes.

tiktoken is lazy-imported at split() time so the class can be constructed and
tested without tiktoken installed.

Usage::

    chunker = Chunker(chunk_token_size=512, chunk_overlap_tokens=64)
    chunks = chunker.split(text)
    # chunks is a list[str], each at most 512 tokens, consecutive chunks
    # share up to 64 tokens of overlap.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_FALLBACK_ENCODING = "cl100k_base"
_CHARS_PER_TOKEN = 4  # rough approximation for character-based fallback


class Chunker:
    """Sliding-window token-aware text splitter.

    tiktoken is lazy-imported at split() time. If tiktoken is not installed,
    a character-based approximation is used instead (1 token ≈ 4 chars).

    Args:
        chunk_token_size: Maximum number of tokens per chunk. Default 512.
        chunk_overlap_tokens: Number of tokens to overlap between consecutive
            chunks. Default 64. Must be less than ``chunk_token_size``.
        model: LiteLLM model string used to resolve the tiktoken encoding.
            If the model is unknown to tiktoken, falls back to cl100k_base.
    """

    def __init__(
        self,
        chunk_token_size: int = 512,
        chunk_overlap_tokens: int = 64,
        model: str = "text-embedding-ada-002",
    ) -> None:
        if chunk_overlap_tokens >= chunk_token_size:
            raise ValueError(
                f"chunk_overlap_tokens ({chunk_overlap_tokens}) must be less than "
                f"chunk_token_size ({chunk_token_size})"
            )
        self._chunk_token_size = chunk_token_size
        self._chunk_overlap_tokens = chunk_overlap_tokens
        self._model = model
        self._enc: Any | None = None  # lazy-loaded on first split()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def split(self, text: str) -> list[str]:
        """Split ``text`` into overlapping token-bounded chunks.

        Args:
            text: Raw text to split. May be empty.

        Returns:
            List of non-empty text strings. Empty input returns an empty list.
            A single-chunk result is returned as a list with one element.
        """
        if not text or not text.strip():
            return []

        enc = self._get_encoding()
        if enc is None:
            return self._char_split(text)

        return self._token_split(text, enc)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_encoding(self) -> Any | None:
        """Return the cached tiktoken encoding, loading it on first call.

        Returns:
            A tiktoken Encoding object, or None if tiktoken is not installed.
        """
        if self._enc is not None:
            return self._enc

        try:
            self._enc = self._resolve_encoding(self._model)
            return self._enc
        except ImportError:
            logger.warning(
                "chunker.tiktoken_not_installed",
                fallback="character_based",
                note="Install tiktoken for token-aware chunking: pip install tiktoken",
            )
            return None

    def _token_split(self, text: str, enc: Any) -> list[str]:
        """Split using tiktoken encoding."""
        tokens = enc.encode(text)
        if not tokens:
            return []

        stride = self._chunk_token_size - self._chunk_overlap_tokens
        chunks: list[str] = []

        start = 0
        while start < len(tokens):
            end = min(start + self._chunk_token_size, len(tokens))
            chunk_tokens = tokens[start:end]
            chunk_text = enc.decode(chunk_tokens).strip()
            if chunk_text:
                chunks.append(chunk_text)
            if end == len(tokens):
                break
            start += stride

        return chunks

    def _char_split(self, text: str) -> list[str]:
        """Fallback character-based splitter (1 token ≈ 4 chars)."""
        char_size = self._chunk_token_size * _CHARS_PER_TOKEN
        char_overlap = self._chunk_overlap_tokens * _CHARS_PER_TOKEN
        stride = char_size - char_overlap

        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = min(start + char_size, len(text))
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end == len(text):
                break
            start += stride

        return chunks

    @staticmethod
    def _resolve_encoding(model: str) -> Any:
        """Resolve a tiktoken encoding for ``model``, falling back to cl100k_base.

        Args:
            model: LiteLLM model string (e.g. ``ollama/nomic-embed-text``).

        Returns:
            A tiktoken Encoding object.

        Raises:
            ImportError: If tiktoken is not installed.
        """
        import tiktoken  # noqa: PLC0415

        # tiktoken knows OpenAI model names. For third-party models (e.g. ollama/*)
        # we strip the provider prefix and try the bare model name, then fall back.
        bare_model = model.split("/")[-1] if "/" in model else model

        for name in (model, bare_model, _FALLBACK_ENCODING):
            try:
                enc = tiktoken.get_encoding(name)
                if name == _FALLBACK_ENCODING and name not in (model, bare_model):
                    logger.warning(
                        "chunker.encoding_fallback",
                        requested_model=model,
                        fallback_encoding=_FALLBACK_ENCODING,
                    )
                return enc
            except Exception:  # noqa: BLE001
                try:
                    enc = tiktoken.encoding_for_model(name)
                    return enc
                except Exception:  # noqa: BLE001, S112
                    continue  # try next name in fallback sequence

        # This path is unreachable if tiktoken ships cl100k_base, but be defensive.
        raise RuntimeError(
            f"Cannot resolve any tiktoken encoding for model={model!r}. "
            "Ensure tiktoken is correctly installed."
        )
