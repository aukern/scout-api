"""MCP server for the sources (ingest) domain.

Exposes two tools to AI agents:
    ingest_url  — ingest a URL source into a collection
    ingest_file — ingest a file (base64-encoded bytes) into a collection

Both tools wrap the same IngestService used by the HTTP layer — no duplicate
logic. The MCP server is integrated into the FastAPI app via create_mcp_app()
in main.py.

fastmcp and mcp are lazy-imported (proxy blocked at install time — they are
production-only dependencies). The module works without them installed; at
runtime the tools require fastmcp>=2.0 and mcp>=1.0.

Integration in main.py (when wired):
    from scout_api.sources.mcp import create_mcp_app as create_sources_mcp_app
    _sources_mcp_app = create_sources_mcp_app()
    app = FastAPI(
        ...,
        routes=list(_sources_mcp_app.routes),
        lifespan=combine_lifespans(lifespan, _sources_mcp_app.lifespan),
    )

DB access from the MCP layer:
    The MCP layer cannot use the FastAPI request/app.state. Instead it builds
    a fresh asyncpg pool from settings on each call. In production this is
    cheap (asyncpg connection pools are lightweight). For testing, override
    by injecting mock adapters into IngestService directly.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def _build_storage_adapter(settings: Any) -> Any:  # pragma: no cover
    """Return the best available storage adapter given the current settings.

    Tries S3StorageAdapter first (requires aioboto3 + S3 env vars). Falls
    back to InMemoryStorageAdapter when aioboto3 is not installed or the
    bucket/region settings are absent.
    """
    from scout_api.sources.storage import InMemoryStorageAdapter  # noqa: PLC0415

    try:
        from scout_api.sources.storage import S3StorageAdapter  # noqa: PLC0415

        s3_bucket = getattr(settings, "s3_bucket_name", None)
        s3_region = getattr(settings, "s3_region", None)
        if s3_bucket and s3_region:
            return S3StorageAdapter(
                bucket=s3_bucket,
                region=s3_region,
                endpoint_url=getattr(settings, "s3_endpoint_url", None),
            )
    except (ImportError, AttributeError):
        pass

    return InMemoryStorageAdapter()


def _build_queue_adapter(settings: Any) -> Any:  # pragma: no cover
    """Return the best available queue adapter given the current settings.

    Tries ArqQueueAdapter first (requires arq + a redis_url setting). Falls
    back to InMemoryQueueAdapter when arq is not installed or redis_url is
    absent.
    """
    from scout_api.sources.queue import InMemoryQueueAdapter  # noqa: PLC0415

    try:
        from scout_api.sources.queue import ArqQueueAdapter  # noqa: PLC0415

        redis_url = getattr(settings, "redis_url", None)
        if redis_url:
            return ArqQueueAdapter(redis_url=redis_url)
    except (ImportError, AttributeError):
        pass

    return InMemoryQueueAdapter()


def _build_mcp_server() -> Any:  # pragma: no cover  # noqa: C901
    """Build the FastMCP server instance.

    Lazy-imports fastmcp so the module can be imported without it installed.

    Returns:
        FastMCP instance with the ingest_url and ingest_file tools registered.

    Raises:
        ImportError: If fastmcp is not installed.
    """
    try:
        from typing import Annotated  # noqa: PLC0415

        from fastmcp import FastMCP  # noqa: PLC0415
        from fastmcp.exceptions import ToolError  # noqa: PLC0415
        from mcp.types import ToolAnnotations  # noqa: PLC0415
        from pydantic import Field  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(
            "fastmcp and mcp are required for the MCP server. "
            "Install them with: pip install fastmcp>=2.0 mcp>=1.0"
        ) from exc

    from scout_api.sources.errors import (  # noqa: PLC0415
        CollectionNotFoundError,
        InvalidOriginError,
        SourceIngestionError,
    )

    mcp: Any = FastMCP(
        "sources",
        instructions=(
            "Provides source ingestion into Collections of knowledge. "
            "Use ingest_url to register a web URL as a source for processing. "
            "Use ingest_file to upload a file (base64-encoded) as a source. "
            "Both tools return immediately with status=pending — the source is "
            "queued for processing and will become ready once chunks are indexed."
        ),
    )

    @mcp.tool(  # type: ignore[untyped-decorator]
        annotations=ToolAnnotations(
            title="Ingest URL",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        )
    )
    async def ingest_url(
        collection_id: Annotated[
            int,
            Field(
                description=(
                    "The integer ID of the collection to ingest the URL into. "
                    "Must be an existing collection."
                ),
                examples=[1, 42],
                gt=0,
            ),
        ],
        url: Annotated[
            str,
            Field(
                description=(
                    "A valid HTTP or HTTPS URL identifying the remote document to ingest. "
                    "Re-ingesting an existing URL refreshes the source and re-queues it "
                    "for processing. Minimum 1 character, maximum 2048 characters."
                ),
                min_length=1,
                max_length=2048,
            ),
        ],
    ) -> dict[str, Any]:
        """Ingest a URL source into a Collection.

        Creates (or refreshes) a Source with the given URL as its origin.
        The source is returned immediately with status=pending — processing
        (fetch, chunk, embed) happens asynchronously in the background.

        Use this when an agent has a URL that should be added to a collection's
        knowledge base. The source becomes searchable after the worker completes
        processing (status transitions to ready).

        Returns:
            Dict with keys:
                source_id (int): The newly created or refreshed source ID.
                collection_id (int): The owning collection.
                origin (str): The canonical URL stored as the source origin.
                status (str): Always "pending" immediately after ingest.
            Example:
                {
                    "source_id": 7,
                    "collection_id": 3,
                    "origin": "https://example.com/paper.pdf",
                    "status": "pending"
                }
        """
        log = logger.bind(tool="ingest_url", collection_id=collection_id)
        try:
            from scout_api.config import get_settings  # noqa: PLC0415
            from scout_api.db import create_pool  # noqa: PLC0415
            from scout_api.sources.repository import SourceRepository  # noqa: PLC0415
            from scout_api.sources.service import IngestService  # noqa: PLC0415

            settings = get_settings()

            try:
                pool = await create_pool(database_url=settings.database_url, max_size=1)
            except Exception as pool_exc:  # noqa: BLE001
                raise ToolError(f"Database connection unavailable: {pool_exc}") from pool_exc

            repo = SourceRepository(pool)
            service = IngestService(
                repo=repo,
                storage=_build_storage_adapter(settings),
                queue=_build_queue_adapter(settings),
            )

            source = await service.ingest_url(collection_id=collection_id, url=url)
            await pool.close()

            return {
                "source_id": source.id,
                "collection_id": source.collection_id,
                "origin": source.origin,
                "status": source.status.value,
            }

        except (CollectionNotFoundError, InvalidOriginError, SourceIngestionError) as exc:
            raise ToolError(exc.message) from exc
        except ToolError:
            raise
        except Exception as exc:
            log.error("mcp.ingest_url.error", error=str(exc))
            raise ToolError(f"ingest_url failed: {exc}") from exc

    @mcp.tool(  # type: ignore[untyped-decorator]
        annotations=ToolAnnotations(
            title="Ingest File",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        )
    )
    async def ingest_file(
        collection_id: Annotated[
            int,
            Field(
                description=(
                    "The integer ID of the collection to ingest the file into. "
                    "Must be an existing collection."
                ),
                examples=[1, 42],
                gt=0,
            ),
        ],
        filename: Annotated[
            str,
            Field(
                description=(
                    "Original filename of the file being ingested (e.g. 'report.pdf'). "
                    "Used to build the stable source origin for re-ingest detection. "
                    "Minimum 1 character, maximum 255 characters."
                ),
                min_length=1,
                max_length=255,
            ),
        ],
        content_base64: Annotated[
            str,
            Field(
                description=(
                    "Base64-encoded bytes of the file to ingest. "
                    "The MCP transport is text-only — encode binary files with "
                    "base64.b64encode(file_bytes).decode() before passing here. "
                    "Maximum decoded size: 50 MB."
                ),
                min_length=1,
            ),
        ],
        content_type: Annotated[
            str,
            Field(
                description=(
                    "MIME type of the file (e.g. 'application/pdf', 'text/plain'). "
                    "Defaults to 'application/octet-stream' when unknown."
                ),
                default="application/octet-stream",
            ),
        ] = "application/octet-stream",
    ) -> dict[str, Any]:
        """Ingest a file into a Collection.

        Accepts base64-encoded file bytes, uploads the file to object storage,
        and queues the source for processing. Re-ingesting a file with the
        same filename refreshes the source and re-queues it.

        The source is returned immediately with status=pending — processing
        (chunk, embed) happens asynchronously in the background.

        Use this when an agent has a local document (PDF, text, HTML) that
        should be added to a collection's knowledge base.

        Returns:
            Dict with keys:
                source_id (int): The newly created or refreshed source ID.
                collection_id (int): The owning collection.
                origin (str): The canonical origin stored (file:// placeholder or s3://).
                status (str): Always "pending" immediately after ingest.
                filename (str): The sanitised filename used for storage.
            Example:
                {
                    "source_id": 8,
                    "collection_id": 3,
                    "origin": "file://3/a1b2c3d4/report.pdf",
                    "status": "pending",
                    "filename": "report.pdf"
                }
        """
        import base64  # noqa: PLC0415
        import os  # noqa: PLC0415

        log = logger.bind(tool="ingest_file", collection_id=collection_id, filename=filename)
        try:
            from scout_api.config import get_settings  # noqa: PLC0415
            from scout_api.db import create_pool  # noqa: PLC0415
            from scout_api.sources.repository import SourceRepository  # noqa: PLC0415
            from scout_api.sources.service import IngestService  # noqa: PLC0415

            try:
                file_bytes = base64.b64decode(content_base64)
            except Exception as decode_exc:  # noqa: BLE001
                raise ToolError(f"content_base64 is not valid base64: {decode_exc}") from decode_exc

            max_bytes = 50 * 1024 * 1024
            if len(file_bytes) > max_bytes:
                raise ToolError(f"File too large: {len(file_bytes)} bytes exceeds 50 MB limit.")

            settings = get_settings()

            try:
                pool = await create_pool(database_url=settings.database_url, max_size=1)
            except Exception as pool_exc:  # noqa: BLE001
                raise ToolError(f"Database connection unavailable: {pool_exc}") from pool_exc

            repo = SourceRepository(pool)
            service = IngestService(
                repo=repo,
                storage=_build_storage_adapter(settings),
                queue=_build_queue_adapter(settings),
            )

            source = await service.ingest_file(
                collection_id=collection_id,
                filename=filename,
                file_bytes=file_bytes,
                content_type=content_type,
            )
            await pool.close()

            safe_filename = os.path.basename(filename.replace("\\", "/")) or "upload"

            return {
                "source_id": source.id,
                "collection_id": source.collection_id,
                "origin": source.origin,
                "status": source.status.value,
                "filename": safe_filename,
            }

        except (CollectionNotFoundError, InvalidOriginError, SourceIngestionError) as exc:
            raise ToolError(exc.message) from exc
        except ToolError:
            raise
        except Exception as exc:
            log.error("mcp.ingest_file.error", error=str(exc))
            raise ToolError(f"ingest_file failed: {exc}") from exc

    return mcp


def create_mcp_app() -> Any:  # pragma: no cover
    """Return the MCP ASGI app serving at /mcp/sources.

    Lazy-builds the FastMCP server. Wire into main.py alongside the search
    and QA MCP apps.

    Returns:
        Starlette ASGI application for the sources MCP server.

    Raises:
        ImportError: If fastmcp is not installed.
    """
    mcp = _build_mcp_server()
    return mcp.http_app(path="/mcp/sources")
