"""Pydantic request/response schemas for the collections domain.

All models use the scout-api glossary vocabulary: 'collection' / 'collections'.
Field names and descriptions avoid the forbidden terms: namespace, tenant, index, corpus.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator


class CreateCollectionRequest(BaseModel):
    """Request body for POST /collections."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description=(
            "Unique name for the collection. "
            "Only letters, numbers, hyphens, and underscores are allowed."
        ),
        examples=["my-research", "support-docs"],
    )

    @field_validator("name")
    @classmethod
    def name_must_be_valid(cls, v: str) -> str:
        """Reject names with characters that would cause SQL or URL issues."""
        v = v.strip()
        if not v:
            raise ValueError("Collection name must not be blank.")
        if not re.match(r"^[a-zA-Z0-9_\-]+$", v):
            raise ValueError(
                "Collection name may only contain letters, numbers, hyphens, and underscores."
            )
        return v


class CollectionResponse(BaseModel):
    """Represents a single collection returned from the API."""

    id: int = Field(..., description="Unique numeric identifier for the collection.")
    name: str = Field(..., description="Unique name of the collection.")

    model_config = {"from_attributes": True}


class ListCollectionsResponse(BaseModel):
    """Response body for GET /collections."""

    collections: list[CollectionResponse] = Field(
        default_factory=list,
        description="All collections, ordered by creation time (oldest first).",
    )
    total: int = Field(..., description="Total number of collections.")
