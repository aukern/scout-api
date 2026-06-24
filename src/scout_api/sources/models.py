"""Pydantic request/response schemas for the sources domain."""

from __future__ import annotations

import datetime

from pydantic import BaseModel, Field, HttpUrl


class IngestUrlRequest(BaseModel):
    """Request body for POST /collections/{id}/sources/url."""

    url: HttpUrl = Field(
        ...,
        description="A valid HTTP or HTTPS URL to ingest.",
        examples=["https://example.com/doc.pdf"],
    )


class SourceResponse(BaseModel):
    """Response body for source ingest endpoints."""

    id: int = Field(..., description="Unique numeric identifier for the source.")
    collection_id: int = Field(..., description="ID of the owning collection.")
    origin: str = Field(
        ...,
        description="URL string or S3 key identifying the source.",
    )
    status: str = Field(
        ...,
        description="Current lifecycle status: pending | processing | ready | failed.",
    )


class SourceDetailResponse(BaseModel):
    """Response body for GET single-source browse endpoints.

    Includes timestamps and ``failed_reason`` so polling agents can confirm
    when a source is ``ready`` or diagnose a ``failed`` source without
    trawling logs.
    """

    id: int = Field(..., description="Unique numeric identifier for the source.")
    collection_id: int = Field(..., description="ID of the owning collection.")
    origin: str = Field(
        ...,
        description="URL string or S3 key identifying the source.",
    )
    status: str = Field(
        ...,
        description="Current lifecycle status: pending | processing | ready | failed.",
    )
    created_at: datetime.datetime = Field(
        ..., description="UTC timestamp when the source was first ingested."
    )
    updated_at: datetime.datetime = Field(
        ..., description="UTC timestamp of the last status change."
    )
    failed_reason: str | None = Field(
        default=None,
        description="Human-readable reason for failure; null when status is not 'failed'.",
    )


class ListSourcesResponse(BaseModel):
    """Response body for GET list-sources browse endpoints."""

    sources: list[SourceDetailResponse] = Field(
        ..., description="Sources in this collection, ordered by created_at ASC."
    )
    total: int = Field(..., description="Total number of sources in this collection.")
