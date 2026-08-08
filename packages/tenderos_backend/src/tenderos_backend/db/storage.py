import asyncio

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from tenderos_backend.core.config import settings


class ObjectStorage:
    """Thin wrapper around a boto3 S3 client pointed at MinIO.

    boto3 is a synchronous library - every method here wraps its blocking
    calls in asyncio.to_thread internally, so no caller (API route or worker
    job) can accidentally call the raw client and stall the event loop the
    way B2's asyncio.run()-per-iteration bug once did.
    """

    def __init__(self) -> None:
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=BotoConfig(signature_version="s3v4"),
        )
        self._bucket = settings.s3_bucket
        self._bucket_ready = False
        self._ensure_bucket_lock = asyncio.Lock()

    async def _ensure_bucket(self) -> None:
        if self._bucket_ready:
            return
        async with self._ensure_bucket_lock:
            if self._bucket_ready:
                return

            def _create_if_missing() -> None:
                try:
                    self._client.head_bucket(Bucket=self._bucket)
                except ClientError:
                    self._client.create_bucket(Bucket=self._bucket)

            await asyncio.to_thread(_create_if_missing)
            self._bucket_ready = True

    async def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        await self._ensure_bucket()
        extra = {"ContentType": content_type} if content_type else {}
        await asyncio.to_thread(
            self._client.put_object, Bucket=self._bucket, Key=key, Body=data, **extra
        )

    async def get(self, key: str) -> bytes:
        await self._ensure_bucket()
        response = await asyncio.to_thread(self._client.get_object, Bucket=self._bucket, Key=key)
        return await asyncio.to_thread(response["Body"].read)


storage = ObjectStorage()
