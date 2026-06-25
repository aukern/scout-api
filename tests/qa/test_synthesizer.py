"""Unit tests for the Synthesizer and helper functions.

All tests use injected fake completion functions — no real LiteLLM calls.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from scout_api.qa.errors import QASynthesisError
from scout_api.qa.synthesizer import (
    Synthesizer,
    build_prompt,
    extract_citations,
)
from scout_api.search.contracts import SearchResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(
    chunk_id: int = 1,
    source_id: int = 10,
    source_origin: str = "https://example.com/doc.pdf",
    content: str = "chunk content",
) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        source_id=source_id,
        collection_id=1,
        content=content,
        score=0.9,
        source_origin=source_origin,
    )


def _make_fake_completion(tokens: list[str]) -> Any:
    """Return a fake async completion function yielding given tokens."""

    class _FakeChunk:
        def __init__(self, text: str) -> None:
            self.choices = [type("c", (), {"delta": type("d", (), {"content": text})()})]

    async def _completion(
        model: str, messages: list, api_base: str, stream: bool, *args: Any
    ) -> Any:
        async def _gen() -> AsyncIterator[_FakeChunk]:
            for t in tokens:
                yield _FakeChunk(t)

        return _gen()

    return _completion


def _make_failing_completion(exc: Exception) -> Any:
    """Return a fake completion function that raises the given exception."""

    async def _completion(
        model: str, messages: list, api_base: str, stream: bool, *args: Any
    ) -> Any:
        raise exc

    return _completion


# ---------------------------------------------------------------------------
# build_prompt tests
# ---------------------------------------------------------------------------


def test_build_prompt_single_source() -> None:
    """build_prompt creates numbered source block for a single source."""
    chunks = [_make_result(chunk_id=1, source_id=10, content="Content A")]
    prompt, source_map = build_prompt(chunks, "What is this?")

    assert "[1] Source: https://example.com/doc.pdf" in prompt
    assert "Content A" in prompt
    assert "Question: What is this?" in prompt
    assert "Answer:" in prompt
    assert 1 in source_map
    assert source_map[1][0].source_id == 10


def test_build_prompt_multiple_sources_numbered() -> None:
    """build_prompt assigns sequential numbers to distinct sources."""
    chunks = [
        _make_result(chunk_id=1, source_id=10, source_origin="https://a.com"),
        _make_result(chunk_id=2, source_id=20, source_origin="https://b.com"),
    ]
    prompt, source_map = build_prompt(chunks, "Question?")

    assert "[1] Source: https://a.com" in prompt
    assert "[2] Source: https://b.com" in prompt
    assert 1 in source_map and source_map[1][0].source_id == 10
    assert 2 in source_map and source_map[2][0].source_id == 20


def test_build_prompt_chunks_from_same_source_merged() -> None:
    """build_prompt merges multiple chunks from the same source under one entry."""
    chunks = [
        _make_result(chunk_id=1, source_id=10, content="Part A"),
        _make_result(chunk_id=2, source_id=10, content="Part B"),
    ]
    prompt, source_map = build_prompt(chunks, "Question?")

    # Only one source-block entry [1] Source: ... (not two)
    # Note: the instruction line contains "[1], [2], etc." so we check
    # that there is exactly one "[1] Source:" entry.
    assert "[1] Source:" in prompt
    # No second source block "[2] Source:" should appear
    assert "[2] Source:" not in prompt
    assert "Part A" in prompt
    assert "Part B" in prompt
    assert len(source_map) == 1
    assert len(source_map[1]) == 2


def test_build_prompt_contains_grounding_instruction() -> None:
    """build_prompt includes the grounding instruction and fallback phrase."""
    chunks = [_make_result()]
    prompt, _ = build_prompt(chunks, "What?")

    assert "ONLY the numbered sources" in prompt
    assert "I don't have enough information to answer that." in prompt


# ---------------------------------------------------------------------------
# extract_citations tests
# ---------------------------------------------------------------------------


def test_extract_citations_finds_markers() -> None:
    """extract_citations returns Citation for each unique [N] marker."""
    result = _make_result(chunk_id=1, source_id=10, source_origin="https://a.com")
    source_map = {1: [result]}
    text = "This answers [1] the question."

    citations = extract_citations(text, source_map)

    assert len(citations) == 1
    assert citations[0].source_id == 10
    assert citations[0].inline_marker == "[1]"
    assert citations[0].chunk_ids == [1]


def test_extract_citations_deduplicates_repeated_markers() -> None:
    """extract_citations returns one Citation per unique N even if [N] appears multiple times."""
    result = _make_result(chunk_id=1, source_id=10)
    source_map = {1: [result]}
    text = "See [1] for details, and also [1] again."

    citations = extract_citations(text, source_map)

    assert len(citations) == 1


def test_extract_citations_multiple_sources() -> None:
    """extract_citations returns Citations in order of first appearance."""
    r1 = _make_result(chunk_id=1, source_id=10, source_origin="https://a.com")
    r2 = _make_result(chunk_id=2, source_id=20, source_origin="https://b.com")
    source_map = {1: [r1], 2: [r2]}
    text = "From [2] and [1]."

    citations = extract_citations(text, source_map)

    assert len(citations) == 2
    assert citations[0].source_id == 20  # [2] appears first
    assert citations[1].source_id == 10


def test_extract_citations_ignores_unknown_markers() -> None:
    """extract_citations ignores [N] markers not in source_map."""
    source_map: dict[int, list[SearchResult]] = {}
    text = "See [1] and [99] for more."

    citations = extract_citations(text, source_map)

    assert citations == []


def test_extract_citations_empty_text() -> None:
    """extract_citations returns empty list for text with no [N] markers."""
    result = _make_result()
    source_map = {1: [result]}

    citations = extract_citations("No citations here.", source_map)

    assert citations == []


def test_extract_citations_chunk_ids_aggregated_from_source() -> None:
    """extract_citations aggregates chunk_ids from all chunks under a source."""
    r1 = _make_result(chunk_id=1, source_id=10)
    r2 = _make_result(chunk_id=2, source_id=10)
    source_map = {1: [r1, r2]}
    text = "See [1]."

    citations = extract_citations(text, source_map)

    assert citations[0].chunk_ids == [1, 2]


# ---------------------------------------------------------------------------
# Synthesizer.stream tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesizer_yields_tokens_then_citations() -> None:
    """Synthesizer.stream yields (token, None) then ("", citations) at end."""
    tokens = ["Hello ", "world [1]."]
    result = _make_result(chunk_id=1, source_id=10)
    source_map = {1: [result]}

    synth = Synthesizer(model="test", _completion_fn=_make_fake_completion(tokens))
    prompt, _ = build_prompt([result], "What?")

    collected: list[tuple[str, Any]] = []
    async for token, citations in synth.stream(prompt, source_map):
        collected.append((token, citations))

    assert len(collected) >= 2
    # Mid-stream items have citations=None
    mid_stream = [(t, c) for t, c in collected if c is None]
    assert len(mid_stream) == len(tokens)
    assert "Hello " in [t for t, _ in mid_stream]

    # Final item has citations list
    final = [(t, c) for t, c in collected if c is not None]
    assert len(final) == 1
    assert final[0][0] == ""
    assert isinstance(final[0][1], list)
    assert len(final[0][1]) == 1
    assert final[0][1][0].source_id == 10


@pytest.mark.asyncio
async def test_synthesizer_stream_raises_synthesis_error_on_exception() -> None:
    """Synthesizer.stream raises QASynthesisError when LiteLLM fails."""
    synth = Synthesizer(
        model="test",
        _completion_fn=_make_failing_completion(RuntimeError("LLM down")),
    )
    source_map = {1: [_make_result()]}

    with pytest.raises(QASynthesisError) as exc_info:
        async for _ in synth.stream("prompt", source_map):
            pass

    assert exc_info.value.code == "QA_SYN_001"
    assert "LLM down" in exc_info.value.message


@pytest.mark.asyncio
async def test_synthesizer_stream_empty_tokens_produces_empty_answer() -> None:
    """Synthesizer.stream with no tokens yields one final item with empty citations."""
    synth = Synthesizer(model="test", _completion_fn=_make_fake_completion([]))
    source_map = {1: [_make_result()]}

    items: list[tuple[str, Any]] = []
    async for token, citations in synth.stream("prompt", source_map):
        items.append((token, citations))

    assert len(items) == 1
    assert items[0][0] == ""
    assert items[0][1] == []


def test_synthesizer_is_insufficient_detects_phrase() -> None:
    """Synthesizer.is_insufficient returns True for the known fallback phrase."""
    synth = Synthesizer(model="test")
    assert synth.is_insufficient("I don't have enough information to answer that.")
    assert synth.is_insufficient("I DON'T HAVE ENOUGH INFORMATION to answer that.")


def test_synthesizer_is_insufficient_returns_false_for_normal_answer() -> None:
    """Synthesizer.is_insufficient returns False for a normal answer."""
    synth = Synthesizer(model="test")
    assert not synth.is_insufficient("Scout API is a knowledge tool [1].")


# ---------------------------------------------------------------------------
# Per-chunk timeout and finish_reason guard tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesizer_stream_raises_on_chunk_timeout() -> None:
    """Synthesizer.stream raises QASynthesisError when a chunk times out."""
    import asyncio

    async def hanging_completion(
        model: str, messages: list, api_base: str, stream: bool, *args: Any
    ) -> Any:
        async def _gen() -> Any:
            yield type(
                "c",
                (),
                {
                    "choices": [
                        type(
                            "ch",
                            (),
                            {
                                "delta": type("d", (), {"content": "partial"})(),
                                "finish_reason": None,
                            },
                        )()
                    ],
                },
            )()
            # Second chunk never arrives — simulate hung provider
            await asyncio.sleep(100)
            yield type("c", (), {"choices": []})()

        return _gen()

    synth = Synthesizer(model="test", _completion_fn=hanging_completion)

    # Temporarily shorten the timeout for test speed
    import scout_api.qa.synthesizer as synth_module

    original_timeout = synth_module._CHUNK_TIMEOUT_SECONDS
    synth_module._CHUNK_TIMEOUT_SECONDS = 0.05

    try:
        with pytest.raises(QASynthesisError) as exc_info:
            async for _ in synth.stream("prompt", {}):
                pass
    finally:
        synth_module._CHUNK_TIMEOUT_SECONDS = original_timeout

    assert "timed out" in exc_info.value.message.lower()
    assert exc_info.value.code == "QA_SYN_001"


@pytest.mark.asyncio
async def test_synthesizer_stream_raises_on_content_filter() -> None:
    """Synthesizer.stream raises QASynthesisError when content_filter is returned."""

    async def filtered_completion(
        model: str, messages: list, api_base: str, stream: bool, *args: Any
    ) -> Any:
        async def _gen() -> Any:
            yield type(
                "c",
                (),
                {
                    "choices": [
                        type(
                            "ch",
                            (),
                            {
                                "delta": type("d", (), {"content": None})(),
                                "finish_reason": "content_filter",
                            },
                        )()
                    ],
                },
            )()

        return _gen()

    synth = Synthesizer(model="test", _completion_fn=filtered_completion)
    source_map = {1: [_make_result()]}

    with pytest.raises(QASynthesisError) as exc_info:
        async for _ in synth.stream("prompt", source_map):
            pass

    assert "content filter" in exc_info.value.message.lower()
    assert exc_info.value.code == "QA_SYN_001"


@pytest.mark.asyncio
async def test_synthesizer_stream_handles_length_truncation() -> None:
    """Synthesizer.stream yields a done chunk (with partial text) on length finish_reason."""

    async def truncated_completion(
        model: str, messages: list, api_base: str, stream: bool, *args: Any
    ) -> Any:
        async def _gen() -> Any:
            # First chunk: normal token
            yield type(
                "c",
                (),
                {
                    "choices": [
                        type(
                            "ch",
                            (),
                            {
                                "delta": type("d", (), {"content": "Partial answer [1]"})(),
                                "finish_reason": None,
                            },
                        )()
                    ],
                },
            )()
            # Second chunk: length truncation
            yield type(
                "c",
                (),
                {
                    "choices": [
                        type(
                            "ch",
                            (),
                            {
                                "delta": type("d", (), {"content": None})(),
                                "finish_reason": "length",
                            },
                        )()
                    ],
                },
            )()

        return _gen()

    result = _make_result(chunk_id=1, source_id=10)
    source_map = {1: [result]}
    synth = Synthesizer(model="test", _completion_fn=truncated_completion)

    items: list[tuple[str, Any]] = []
    async for token, citations in synth.stream("prompt", source_map):
        items.append((token, citations))

    # Should still yield a done item with whatever citations were found
    final = [(t, c) for t, c in items if c is not None]
    assert len(final) == 1
    assert final[0][0] == ""
    # Citations from partial text "[1]" should be present
    assert len(final[0][1]) == 1
