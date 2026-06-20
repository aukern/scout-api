"""Shared error envelope and error code registry for scout-api.

All HTTP error responses follow the structure:
    {"error": {"code": "ERROR_CODE", "message": "Human-readable description"}}

Domain-specific error codes are defined in each domain's errors.py module.
This module defines the shared envelope and base app errors.
"""

from __future__ import annotations

from fastapi.responses import JSONResponse

# ---------------------------------------------------------------------------
# Error response envelope
# ---------------------------------------------------------------------------


def error_response(code: str, message: str, status_code: int) -> JSONResponse:
    """Build a standard JSON error response.

    Args:
        code: Machine-readable error code (e.g. "COLLECTION_ALREADY_EXISTS").
        message: Human-readable message safe for API consumers.
        status_code: HTTP status code.

    Returns:
        JSONResponse with the standard error envelope.
    """
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


# ---------------------------------------------------------------------------
# Base domain exception
# ---------------------------------------------------------------------------


class ScoutError(Exception):
    """Base exception for all scout-api domain errors.

    Args:
        message: Human-readable description (safe for API responses).
        code: Machine-readable error code.
        status_code: HTTP status code to return.
    """

    def __init__(self, message: str, code: str, status_code: int) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code

    def to_response(self) -> JSONResponse:
        """Convert this exception to a FastAPI JSONResponse."""
        return error_response(self.code, self.message, self.status_code)
