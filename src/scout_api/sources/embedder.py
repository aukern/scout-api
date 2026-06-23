"""LiteLLM embedding wrapper for source chunk embedding.

Embeds a single text string using the configured LiteLLM model. Batching is
intentionally not used here — individual calls give better error isolation per
chunk (one bad chunk does not fail the entire source).

Embedding dimension probing:
  On first use, the embedder probes the model with a single short string and
  records the resulting vector length. Subsequent calls validate that the returned
  vector matches this dimension. This catches model-switch bugs early.

litellm is lazy-imported at call time so the class can be constructed and tested
without litellm installed. Tests inject a fake embedding function via the
``_embed_fn`` constructor argument.

Usage::

    embedder = Embedder(model="ollama/nomic-embed-text", api_base="http://localhost:11434")
    vector = await embedder.embed("hello world")
    # len(vector) == 768 for nomic-embed-text
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_PROBE_TEXT = "dimension probe"

# Type alias for the embedding function signature
_EmbedFn = Callable[[str, str, str], Coroutine[Any, Any, list[float]]]


class Embedder:
    """LiteLLM-backed text embedder with dimension probing.

    Args:
        model: LiteLLM model string (e.g. ``ollama/nomic-embed-text``,
               ``text-embedding-ada-002``).
        api_base: Optional API base URL for local models (e.g. Ollama).
        _embed_fn: Optional override for the embedding function — used in tests
            to bypass litellm without installing it. Signature:
            ``async def fn(text, model, api_base) -> list[float]``.
    """

    def __init__(
        self,
        model: str,
        api_base: str = "",
        _embed_fn: _EmbedFn | None = None,
    ) -> None:
        self._model = model
        self._api_base = api_base
        self._dim: int | None = None
        self._embed_fn = _embed_fn  # test injection point

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def dim(self) -> int | None:
        """Cached embedding dimension; None before the first embed call."""
        return self._dim

    async def probe(self) -> int:
        """Probe the model and return the embedding dimension.

        Sends a short text to the model and records the vector length.
        Safe to call multiple times — subsequent calls are no-ops and return
        the cached dimension.

        Returns:
            Embedding dimension as an integer.
        """
        if self._dim is None:
            vector = await self._call_model(_PROBE_TEXT)
            self._dim = len(vector)
            logger.info(
                "embedder.probe",
                model=self._model,
                dimension=self._dim,
            )
        return self._dim

    async def embed(self, text: str) -> list[float]:
        """Embed a single text string.

        Args:
            text: The text to embed. Must be non-empty.

        Returns:
            Float vector from the model.

        Raises:
            ValueError: If ``text`` is empty.
            RuntimeError: On LiteLLM API error.
        """
        if not text or not text.strip():
            raise ValueError("Cannot embed empty text.")

        vector = await self._call_model(text)

        # Cache dimension on first embed; validate on subsequent calls.
        if self._dim is None:
            self._dim = len(vector)
        elif len(vector) != self._dim:
            raise RuntimeError(
                f"Embedding dimension changed mid-run: expected {self._dim}, "
                f"got {len(vector)}. Do not switch models during processing."
            )

        return vector

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _call_model(self, text: str) -> list[float]:
        """Call the embedding function (litellm or injected test fn).

        Args:
            text: Text to embed.

        Returns:
            List of floats representing the embedding vector.

        Raises:
            RuntimeError: On any embedding error.
        """
        if self._embed_fn is not None:
            # Test injection — bypass litellm
            return await self._embed_fn(text, self._model, self._api_base)

        return await self._call_litellm(text)

    async def _call_litellm(self, text: str) -> list[float]:
        """Call litellm.aembedding (lazy import — litellm need not be installed at import time).

        Args:
            text: Text to embed.

        Returns:
            Embedding vector as a list of floats.

        Raises:
            ImportError: If litellm is not installed.
            RuntimeError: On any LiteLLM API error.
        """
        try:
            import litellm  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "litellm is required for embedding. Install it with: pip install litellm"
            ) from exc

        kwargs: dict[str, object] = {"model": self._model, "input": [text]}
        if self._api_base:
            kwargs["api_base"] = self._api_base

        try:
            response = await litellm.aembedding(**kwargs)
            return list(response.data[0]["embedding"])
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "embedder.error",
                model=self._model,
                error=str(exc),
            )
            raise RuntimeError(f"LiteLLM embedding call failed: {exc}") from exc
