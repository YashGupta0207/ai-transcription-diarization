"""
S3-compatible storage backend.
Works with Backblaze B2 (S3-compatible API), AWS S3, or any compatible provider
just by changing S3_ENDPOINT_URL. This is the recommended PRODUCTION backend
because Backblaze B2 offers a generous free tier (10GB storage / 1GB egress per day)
and is trivial to wire into Render deployments (no persistent disk needed).
"""
import os
import tempfile
from typing import BinaryIO

import boto3
from botocore.client import Config

from app.config import settings
from app.storage.base import StorageBackend


class S3Storage(StorageBackend):
    def __init__(self):
        self.bucket = settings.S3_BUCKET_NAME
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT_URL or None,
            aws_access_key_id=settings.S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
            region_name=settings.S3_REGION,
            config=Config(signature_version="s3v4"),
        )

    def save(self, key: str, file_obj: BinaryIO) -> str:
        self.client.upload_fileobj(file_obj, self.bucket, key)
        return key

    def get_local_path(self, key: str) -> str:
        # Worker needs a local file to run ffmpeg / send to providers that need file paths.
        suffix = os.path.splitext(key)[1]
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        self.client.download_fileobj(self.bucket, key, tmp)
        tmp.close()
        return tmp.name

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def get_url(self, key: str, expires_in: int = 3600) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_in,
        )
