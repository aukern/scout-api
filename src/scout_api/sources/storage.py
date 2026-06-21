"""Storage adapter abstractions for source file uploads.

The AbstractStorageAdapter Protocol defines the interface for object storage.
This allows the service layer to be tested without a real S3 bucket by
injecting InMemoryStorageAdapter.

Production wiring:
  S3StorageAdapter is wired via get_storage() in dependencies.py.
  It requires aioboto3 and the AWS_* / S3_* environment variables.

Test wiring:
  InMemoryStorageAdapter stores bytes in a plain dict — zero dependencies.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AbstractStorageAdapter(Protocol):
    """Protocol for object storage backends.

    Both S3StorageAdapter (production) and InMemoryStorageAdapter (tests)
    satisfy this protocol. The service depends only on the protocol — never
    on a concrete implementation.
    """

    async def upload(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Upload bytes to object storage and return the storage key.

        Args:
            key: The storage path / object key.
            data: Raw file bytes to store.
            content_type: MIME type of the content.

        Returns:
            The key used to store the object (same as ``key``).
        """
        ...

    async def delete(self, key: str) -> None:
        """Delete an object from storage.

        Args:
            key: The storage path / object key to remove.

        Returns:
            None. Silently succeeds if the key does not exist.
        """
        ...


class InMemoryStorageAdapter:
    """In-memory storage adapter for tests and local development.

    Stores uploaded bytes in a plain dict keyed by the storage key.
    Instantiate once per test and inject via override_dependency().

    Attributes:
        store: Dict mapping storage keys to raw bytes.
    """

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    async def upload(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Store bytes in memory and return the key."""
        self.store[key] = data
        return key

    async def delete(self, key: str) -> None:
        """Remove the key from the in-memory store if it exists."""
        self.store.pop(key, None)


class S3StorageAdapter:
    """AWS S3 storage adapter for production.

    Wraps aioboto3 to upload and delete objects in an S3 bucket.
    Requires aioboto3 to be installed (``pip install aioboto3``).

    Raises ImportError on instantiation if aioboto3 is not installed,
    so the application will fail fast at startup rather than at request time.

    Configuration (from environment via Settings):
        S3_BUCKET_NAME: Target bucket.
        S3_REGION: AWS region.
        S3_ENDPOINT_URL: Override endpoint (e.g. http://localhost:4566 for localstack).
        AWS_ACCESS_KEY_ID: AWS credential (or use IRSA in prod).
        AWS_SECRET_ACCESS_KEY: AWS credential (or use IRSA in prod).
    """

    def __init__(
        self,
        bucket: str,
        region: str,
        endpoint_url: str | None = None,
    ) -> None:
        try:
            import aioboto3  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "aioboto3 is required for S3StorageAdapter. "
                "Install it with: pip install 'aioboto3>=13.0'"
            ) from exc

        self._aioboto3 = aioboto3
        self._bucket = bucket
        self._region = region
        self._endpoint_url = endpoint_url

    async def upload(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Upload bytes to S3 and return the storage key.

        Args:
            key: The S3 object key.
            data: Raw file bytes.
            content_type: MIME type of the content.

        Returns:
            The S3 object key (same as ``key``).
        """
        session = self._aioboto3.Session()
        async with session.client(
            "s3",
            region_name=self._region,
            endpoint_url=self._endpoint_url,
        ) as s3:
            await s3.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
        return key

    async def delete(self, key: str) -> None:
        """Delete an object from S3.

        Args:
            key: The S3 object key to delete.
        """
        session = self._aioboto3.Session()
        async with session.client(
            "s3",
            region_name=self._region,
            endpoint_url=self._endpoint_url,
        ) as s3:
            await s3.delete_object(Bucket=self._bucket, Key=key)
