"""Synthesizer — builds prompts, streams LiteLLM completions, extracts citations.

The Synthesizer is responsible for:
1. Grouping SearchResult chunks by Source and numbering them 1..N.
2. Building a grounded prompt that instructs the LLM to answer ONLY from
   the numbered sources.
3. Streaming the LiteLLM completion token-by-token via an async generator.
4. Extracting Citations from the accumulated answer text after streaming ends.

LiteLLM is lazy-imported at call time so the class can be constructed and
tested without litellm installed. Tests inject a fake async generator via the
``_completion_fn`` constructor argument.

Grounding guarantee:
    The prompt explicitly states "Answer using ONLY the numbered sources."
    If no chunks are available the service raises QANoContextError before
    calling the synthesizer — the synthesizer always receives at least one
    chunk.

Citation detection:
    After the full answer text is accumulated, a regex scans for [N] markers
    and maps them back to Citation objects using the source_number → SearchResult
    lookup built during prompt construction.
"""

from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

import structlog

from scout_api.qa.contracts import Citation
from scout_api.search.contracts import SearchResult

logger = structlog.get_logger(__name__)

_CITATION_RE = re.compile(r"\[(\d+)\]")

# Maximum question length accepted for synthesis (characters).
_MAX_QUESTION_LEN = 4000

# The phrase the LLM uses when context is insufficient.
_INSUFFICIENT_PHRASE = "i don't have enough information"

# Per-chunk timeout — if a provider goes silent mid-stream, abort after this many seconds.
_CHUNK_TIMEOUT_SECONDS: float = 30.0

# Prompt version — bump this when the prompt in config/prompts/qa_synthesis.yaml changes.
_PROMPT_VERSION = "1.0"

# Type for the injectable completion function (used in tests).
# Signature matches the litellm.acompletion interface we use.
_CompletionFn = Callable[
    [str, list[dict[str, str]], str, bool],
    Coroutine[Any, Any, Any],
]


def _group_by_source(
    chunks: list[SearchResult],
) -> dict[int, list[SearchResult]]:
    """Group SearchResult chunks by source_id preserving insertion order.

    Args:
        chunks: List of SearchResult objects (ordered by similarity score).

    Returns:
        Dict mapping source_id → list of SearchResult, in order of first
        appearance. Sources that appear earlier (higher similarity) come first.
    """
    grouped: dict[int, list[SearchResult]] = defaultdict(list)
    seen_order: list[int] = []
    for chunk in chunks:
        if chunk.source_id not in grouped:
            seen_order.append(chunk.source_id)
        grouped[chunk.source_id].append(chunk)
    # Return an ordered dict preserving first-seen order
    return {sid: grouped[sid] for sid in seen_order}


def build_prompt(
    chunks: list[SearchResult], question_text: str
) -> tuple[str, dict[int, list[SearchResult]]]:
    """Build the grounded synthesis prompt and source number mapping.

    Groups chunks by Source, numbers them 1..N, and constructs the prompt.
    The returned source_map maps source number (1-based int) to the list of
    SearchResult objects that contributed to that source's prompt entry.

    Args:
        chunks: Ranked SearchResult objects from QARepository.retrieve_chunks().
        question_text: The user's question text.

    Returns:
        Tuple of (prompt_string, source_map) where source_map[N] is the
        list of SearchResult objects under source number N.
    """
    by_source = _group_by_source(chunks)
    source_map: dict[int, list[SearchResult]] = {}
    source_blocks: list[str] = []

    for idx, (_source_id, results) in enumerate(by_source.items(), start=1):
        source_map[idx] = results
        origin = results[0].source_origin
        content_parts = "\n    ".join(r.content for r in results)
        block = f"[{idx}] Source: {origin}\n    {content_parts}"
        source_blocks.append(block)

    sources_text = "\n\n".join(source_blocks)

    prompt = (
        "You are a research assistant. Answer using ONLY the numbered sources below.\n"
        "Cite sources inline as [1], [2], etc. "
        "If the context is insufficient to answer, say "
        '"I don\'t have enough information to answer that."\n\n'
        f"{sources_text}\n\n"
        f"Question: {question_text}\n"
        "Answer:"
    )

    return prompt, source_map


def extract_citations(
    answer_text: str,
    source_map: dict[int, list[SearchResult]],
) -> list[Citation]:
    """Extract citations from the accumulated answer text.

    Scans for [N] markers in order of appearance and maps each unique N to
    a Citation using the source_map built during prompt construction.

    Args:
        answer_text: The full accumulated answer text from the LLM.
        source_map: Maps source number (1-based int) to SearchResult list.

    Returns:
        List of Citation objects in order of first appearance in the text.
        Duplicate markers (same N appearing twice) produce one Citation.
    """
    seen: set[int] = set()
    citations: list[Citation] = []

    for match in _CITATION_RE.finditer(answer_text):
        n = int(match.group(1))
        if n not in source_map or n in seen:
            continue
        seen.add(n)
        results = source_map[n]
        citations.append(
            Citation(
                source_id=results[0].source_id,
                source_origin=results[0].source_origin,
                chunk_ids=[r.chunk_id for r in results],
                inline_marker=f"[{n}]",
            )
        )

    return citations


async def _default_completion(
    model: str,
    messages: list[dict[str, str]],
    api_base: str,
    stream: bool,
    *_: Any,
) -> Any:
    """Default completion function using litellm.acompletion.

    Lazy-imports litellm so the module can be used without it installed.

    Args:
        model: LiteLLM model string.
        messages: Chat messages list.
        api_base: Optional API base URL for local models.
        stream: Must be True — streaming is always enabled here.

    Returns:
        Async iterable of completion chunks from litellm.
    """
    import litellm  # noqa: PLC0415

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": stream,
    }
    if api_base:
        kwargs["api_base"] = api_base

    return await litellm.acompletion(**kwargs)


class Synthesizer:
    """Streams LiteLLM completions and extracts citations.

    Args:
        model: LiteLLM model string (e.g. "gpt-4o-mini", "ollama/llama3").
        api_base: Optional API base URL for local models (e.g. Ollama).
        _completion_fn: Injectable completion function for testing. Must
            accept (model, messages, api_base, stream) and return an async
            iterable of chunks with choices[0].delta.content.
    """

    def __init__(
        self,
        model: str,
        api_base: str = "",
        _completion_fn: _CompletionFn | None = None,
    ) -> None:
        self._model = model
        self._api_base = api_base
        self._completion_fn = _completion_fn or _default_completion

    async def stream(
        self,
        prompt: str,
        source_map: dict[int, list[SearchResult]],
    ) -> AsyncIterator[tuple[str, list[Citation] | None]]:
        """Stream the LLM answer and yield (token, citations) tuples.

        Yields (token_text, None) for each mid-stream token.
        Yields ("", citations) as the final item when streaming ends.

        Citations are extracted from the accumulated answer text after
        all tokens are received. The final yield carries the citation list
        (may be empty if the LLM cited nothing or gave an insufficient-context
        response).

        Args:
            prompt: The grounded synthesis prompt from build_prompt().
            source_map: Source number → SearchResult list for citation extraction.

        Yields:
            Tuple of (token_text: str, citations: list[Citation] | None).
            citations is None for mid-stream tokens; a list on the final yield.

        Raises:
            QASynthesisError: If the LiteLLM call fails.
        """
        from scout_api.qa.errors import QASynthesisError  # noqa: PLC0415

        messages = [{"role": "user", "content": prompt}]
        accumulated = ""

        logger.info(
            "qa.synthesizer.stream_start",
            model=self._model,
            prompt_version=_PROMPT_VERSION,
        )

        try:
            response = await self._completion_fn(
                self._model,
                messages,
                self._api_base,
                True,
            )

            # Per-chunk timeout: if a provider goes silent mid-stream, abort rather than
            # hanging the WebSocket connection indefinitely. _CHUNK_TIMEOUT_SECONDS gives
            # the provider up to 30s per chunk — well above p99 latency for any major LLM.
            it = response.__aiter__()
            while True:
                try:
                    chunk = await asyncio.wait_for(it.__anext__(), timeout=_CHUNK_TIMEOUT_SECONDS)
                except StopAsyncIteration:
                    break
                except TimeoutError as te:
                    logger.error(
                        "qa.synthesizer.chunk_timeout",
                        model=self._model,
                        accumulated_tokens=len(accumulated),
                    )
                    raise QASynthesisError(detail="LLM stream timed out mid-response") from te

                # finish_reason guard: handle content_filter and length truncation.
                finish_reason = None
                if chunk.choices:
                    finish_reason = getattr(chunk.choices[0], "finish_reason", None)

                if finish_reason == "content_filter":
                    logger.error("qa.synthesizer.content_filter", model=self._model)
                    raise QASynthesisError(detail="LLM response blocked by content filter")

                if finish_reason == "length":
                    # Truncated response — log a warning but still deliver what we have.
                    # Citations will be extracted from partial text; the done frame is still sent.
                    logger.warning(
                        "qa.synthesizer.response_truncated",
                        model=self._model,
                        accumulated_tokens=len(accumulated),
                    )
                    break

                token = (chunk.choices[0].delta.content or "") if chunk.choices else ""
                if token:
                    accumulated += token
                    yield token, None

        except QASynthesisError:
            raise
        except Exception as exc:
            logger.error(
                "qa.synthesizer.stream_error",
                model=self._model,
                error=str(exc),
            )
            raise QASynthesisError(detail=str(exc)) from exc

        # Stream finished — extract citations from accumulated text
        citations = extract_citations(accumulated, source_map)

        logger.info(
            "qa.synthesizer.stream_complete",
            model=self._model,
            prompt_version=_PROMPT_VERSION,
            output_tokens=len(accumulated),
            citations=len(citations),
            insufficient=_INSUFFICIENT_PHRASE in accumulated.lower(),
        )

        yield "", citations

    def is_insufficient(self, answer_text: str) -> bool:
        """Return True if the answer signals insufficient context.

        Args:
            answer_text: The accumulated LLM answer text.

        Returns:
            True if the LLM used the insufficient-context fallback phrase.
        """
        return _INSUFFICIENT_PHRASE in answer_text.lower()
