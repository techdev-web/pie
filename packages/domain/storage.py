"""Object storage: S3-compatible (MinIO) or local filesystem."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from pathlib import Path

import boto3
from botocore.client import Config

from packages.config import Settings, get_settings


class StorageBackend(ABC):
    @abstractmethod
    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        ...

    @abstractmethod
    def get_bytes(self, key: str) -> bytes:
        ...

    @abstractmethod
    def exists(self, key: str) -> bool:
        ...

    @abstractmethod
    def uri_for(self, key: str) -> str:
        ...


class FilesystemStorage(StorageBackend):
    def __init__(self, root: str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        path = self._path(key)
        if path.exists():
            return self.uri_for(key)
        path.write_bytes(data)
        return self.uri_for(key)

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def uri_for(self, key: str) -> str:
        return f"fs://{key}"


class S3Storage(StorageBackend):
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=Config(signature_version="s3v4"),
        )
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            try:
                self.client.create_bucket(Bucket=self.bucket)
            except Exception:
                pass

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        if self.exists(key):
            return self.uri_for(key)
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        return self.uri_for(key)

    def get_bytes(self, key: str) -> bytes:
        obj = self.client.get_object(Bucket=self.bucket, Key=key)
        return obj["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def uri_for(self, key: str) -> str:
        return f"s3://{self.bucket}/{key}"


def document_storage_key(tenant_id: str, content_hash: str) -> str:
    return f"tenants/{tenant_id}/documents/{content_hash}"


def page_image_storage_key(tenant_id: str, document_id: str, page_number: int) -> str:
    return f"tenants/{tenant_id}/documents/{document_id}/pages/{page_number}.png"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def get_storage(settings: Settings | None = None) -> StorageBackend:
    settings = settings or get_settings()
    if settings.storage_backend == "fs":
        return FilesystemStorage(settings.fs_storage_path)
    return S3Storage(settings)
