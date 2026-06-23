"""Unit tests for the LiteLLM Embedder.

Tests use the ``_embed_fn`` injection point — no litellm installation required.
This validates dimension probing, caching, validation, and error handling
without any external API calls.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from scout_api.sources.embedder import Embedder

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_embed_fn(vector: list[float]) -> AsyncMock:
    """Return an async mock embed_fn that returns a fixed vector."""
    return AsyncMock(return_value=vector)


def _make_embed_fn_that_raises(exc: Exception) -> AsyncMock:
    """Return an async mock embed_fn that raises exc."""
    return AsyncMock(side_effect=exc)


def _make_embedder(vector: list[float], model: str = "test-model", api_base: str = "") -> Embedder:
    """Construct an Embedder with an injected fake embed function."""
    return Embedder(model=model, api_base=api_base, _embed_fn=_make_embed_fn(vector))


# ---------------------------------------------------------------------------
# Dimension probing
# ---------------------------------------------------------------------------


class TestEmbedderProbe:
    async def test_probe_returns_vector_length(self) -> None:
        embedder = _make_embedder([0.1] * 768)
        dim = await embedder.probe()
        assert dim == 768

    async def test_probe_caches_dimension(self) -> None:
        embed_fn = _make_embed_fn([0.1] * 1536)
        embedder = Embedder(model="test", _embed_fn=embed_fn)
        await embedder.probe()
        await embedder.probe()  # second call should NOT re-invoke embed_fn
        assert embed_fn.await_count == 1

    async def test_probe_sets_dim_property(self) -> None:
        embedder = _make_embedder([0.5] * 384)
        assert embedder.dim is None
        await embedder.probe()
        assert embedder.dim == 384

    def test_dim_is_none_before_any_call(self) -> None:
        embedder = Embedder(model="any-model")
        assert embedder.dim is None


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------


class TestEmbedderEmbed:
    async def test_embed_returns_vector(self) -> None:
        embedder = _make_embedder([0.1, 0.2, 0.3])
        result = await embedder.embed("hello world")
        assert result == [0.1, 0.2, 0.3]

    async def test_embed_caches_dim_on_first_call(self) -> None:
        embedder = _make_embedder([0.0] * 512)
        await embedder.embed("text")
        assert embedder.dim == 512

    async def test_embed_validates_consistent_dimension(self) -> None:
        """Second embed with different vector length raises RuntimeError."""
        first_fn = _make_embed_fn([0.1] * 768)
        second_fn = _make_embed_fn([0.1] * 512)

        embedder = Embedder(model="test", _embed_fn=first_fn)
        await embedder.embed("first text")

        # Swap the fn to return a different dimension
        embedder._embed_fn = second_fn  # noqa: SLF001
        with pytest.raises(RuntimeError, match="dimension changed"):
            await embedder.embed("second text")

    async def test_embed_empty_text_raises_value_error(self) -> None:
        embedder = _make_embedder([0.1])
        with pytest.raises(ValueError, match="empty"):
            await embedder.embed("")

    async def test_embed_whitespace_only_raises_value_error(self) -> None:
        embedder = _make_embedder([0.1])
        with pytest.raises(ValueError, match="empty"):
            await embedder.embed("   ")

    async def test_embed_passes_text_model_api_base_to_fn(self) -> None:
        embed_fn = _make_embed_fn([0.1] * 10)
        embedder = Embedder(model="my-model", api_base="http://localhost:11434", _embed_fn=embed_fn)
        await embedder.embed("test text")

        embed_fn.assert_awaited_once()
        args = embed_fn.call_args.args
        assert args[0] == "test text"
        assert args[1] == "my-model"
        assert args[2] == "http://localhost:11434"

    async def test_embed_raises_runtime_error_on_fn_failure(self) -> None:
        embed_fn = _make_embed_fn_that_raises(RuntimeError("LiteLLM embedding call failed: boom"))
        embedder = Embedder(model="test-model", _embed_fn=embed_fn)
        with pytest.raises(RuntimeError, match="LiteLLM embedding call failed"):
            await embedder.embed("text")


# ---------------------------------------------------------------------------
# Dimension matches model (acceptance criterion)
# ---------------------------------------------------------------------------


class TestEmbedderCallLitellm:
    """Tests for the _call_litellm path (covers lines 138, 153-173).

    We mock the import of litellm rather than installing it.
    """

    async def test_call_litellm_raises_import_error_when_not_installed(self) -> None:
        """If litellm is not importable, _call_litellm raises ImportError."""
        import builtins
        from unittest.mock import patch

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):  # type: ignore[no-untyped-def]
            if name == "litellm":
                raise ImportError("No module named 'litellm'")
            return real_import(name, *args, **kwargs)

        embedder = Embedder(model="test-model")  # no _embed_fn → uses litellm path

        with patch("builtins.__import__", side_effect=mock_import):
            with pytest.raises(ImportError, match="litellm is required"):
                await embedder._call_litellm("text")  # noqa: SLF001

    async def test_call_litellm_with_api_base_passes_kwarg(self) -> None:
        """_call_litellm passes api_base to litellm when set."""
        import types
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_response = MagicMock()
        mock_response.data = [{"embedding": [0.1, 0.2]}]

        mock_litellm = types.ModuleType("litellm")
        mock_litellm.aembedding = AsyncMock(return_value=mock_response)  # type: ignore[attr-defined]

        embedder = Embedder(model="my-model", api_base="http://localhost:11434")

        with patch.dict("sys.modules", {"litellm": mock_litellm}):
            result = await embedder._call_litellm("test")  # noqa: SLF001

        assert result == [0.1, 0.2]
        call_kwargs = mock_litellm.aembedding.call_args.kwargs  # type: ignore[attr-defined]
        assert call_kwargs.get("api_base") == "http://localhost:11434"

    async def test_call_litellm_without_api_base_omits_kwarg(self) -> None:
        """_call_litellm does NOT pass api_base when it is empty."""
        import types
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_response = MagicMock()
        mock_response.data = [{"embedding": [0.5]}]

        mock_litellm = types.ModuleType("litellm")
        mock_litellm.aembedding = AsyncMock(return_value=mock_response)  # type: ignore[attr-defined]

        embedder = Embedder(model="my-model")  # no api_base

        with patch.dict("sys.modules", {"litellm": mock_litellm}):
            result = await embedder._call_litellm("test")  # noqa: SLF001

        assert result == [0.5]
        call_kwargs = mock_litellm.aembedding.call_args.kwargs  # type: ignore[attr-defined]
        assert "api_base" not in call_kwargs

    async def test_call_litellm_raises_runtime_on_exception(self) -> None:
        """Any litellm exception is wrapped in RuntimeError."""
        import types
        from unittest.mock import AsyncMock, patch

        mock_litellm = types.ModuleType("litellm")
        mock_litellm.aembedding = AsyncMock(side_effect=Exception("timeout"))  # type: ignore[attr-defined]

        embedder = Embedder(model="test")

        with patch.dict("sys.modules", {"litellm": mock_litellm}):
            with pytest.raises(RuntimeError, match="LiteLLM embedding call failed"):
                await embedder._call_litellm("text")  # noqa: SLF001


class TestEmbedderDimensionMatchesModelProbe:
    """Verifies: Vector column dimension matches the model (not hardcoded)."""

    async def test_embedder_dimension_matches_model_probe(self) -> None:
        """The dimension from embed() equals the dimension from probe()."""
        vector = [0.42] * 1024
        # Use a fresh embed_fn for each call so we get consistent 1024-dim vectors
        embed_fn = _make_embed_fn(vector)
        embedder = Embedder(model="some-1024-dim-model", _embed_fn=embed_fn)
        probe_dim = await embedder.probe()
        result = await embedder.embed("hello")

        assert len(result) == probe_dim == 1024
