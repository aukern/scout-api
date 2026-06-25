"""Application configuration for scout-api.

Loads settings from config/app_config.yaml and config/app_config.{env}.yaml,
overridden by APP_* environment variables.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _project_root() -> Path:
    """Resolve project root regardless of working directory."""
    # src/scout_api/config.py → ../../ = project root
    return Path(__file__).parent.parent.parent


class Settings(BaseSettings):
    """Application settings — sourced from environment variables.

    All fields can be overridden via environment variables with the same name.
    The DATABASE_URL is the primary required setting for this slice.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    database_url: str = "postgresql://appuser:apppassword@localhost:5432/appdb"
    max_connections: int = 10

    # Application
    app_env: str = "dev"
    app_port: int = 8000
    log_level: str = "INFO"

    # Object storage (S3 / localstack)
    s3_bucket_name: str = ""
    s3_region: str = "us-east-1"
    s3_endpoint_url: str = ""  # Override for localstack: http://localhost:4566

    # Background jobs (arq + Redis)
    redis_url: str = ""
    arq_concurrency: int = 10

    # Embedding model (used by the processing worker)
    embedding_model: str = "text-embedding-ada-002"
    ollama_api_base: str = ""  # e.g. http://localhost:11434 for local Ollama

    # Chunking parameters (token-aware sliding window)
    chunk_token_size: int = 512
    chunk_overlap_tokens: int = 64

    # Semantic search cache (slice 23)
    search_cache_ttl_seconds: int = 300


def get_settings() -> Settings:
    """Return application settings, reading from environment."""
    return Settings()
