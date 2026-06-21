"""Pydantic request/response schemas for the sources domain."""

from __future__ import annotations

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
