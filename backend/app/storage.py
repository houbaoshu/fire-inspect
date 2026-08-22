from __future__ import annotations

import hashlib
import shutil
import uuid
from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path
from typing import Any

import boto3

from .config import get_settings


class ObjectStorage(ABC):
    @abstractmethod
    def put_bytes(self, key: str, data: bytes, content_type: str) -> None: ...

    @abstractmethod
    def read_bytes(self, key: str) -> bytes: ...

    @abstractmethod
    def begin_multipart(self, key: str, content_type: str) -> str: ...

    @abstractmethod
    def put_part(self, key: str, upload_id: str, number: int, data: bytes) -> str: ...

    @abstractmethod
    def presign_part(self, key: str, upload_id: str, number: int) -> str | None: ...

    @abstractmethod
    def complete_multipart(
        self, key: str, upload_id: str, parts: list[dict[str, Any]], content_type: str
    ) -> None: ...

    @abstractmethod
    def presign_get(self, key: str, expires: int = 900) -> str | None: ...


class LocalStorage(ObjectStorage):
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / ".uploads").mkdir(exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValueError("非法对象路径")
        return path

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def read_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def begin_multipart(self, key: str, content_type: str) -> str:
        upload_id = uuid.uuid4().hex
        (self.root / ".uploads" / upload_id).mkdir(parents=True)
        return upload_id

    def put_part(self, key: str, upload_id: str, number: int, data: bytes) -> str:
        part = self.root / ".uploads" / upload_id / f"{number:08d}.part"
        if not part.parent.is_dir():
            raise FileNotFoundError("上传会话不存在")
        part.write_bytes(data)
        return hashlib.md5(data).hexdigest()  # noqa: S324 - S3-compatible ETag, not security

    def presign_part(self, key: str, upload_id: str, number: int) -> str | None:
        return None

    def complete_multipart(
        self, key: str, upload_id: str, parts: list[dict[str, Any]], content_type: str
    ) -> None:
        upload_dir = self.root / ".uploads" / upload_id
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as output:
            for part in sorted(upload_dir.glob("*.part")):
                with part.open("rb") as source:
                    shutil.copyfileobj(source, output)
        shutil.rmtree(upload_dir)

    def presign_get(self, key: str, expires: int = 900) -> str | None:
        return None


class S3Storage(ObjectStorage):
    def __init__(self) -> None:
        settings = get_settings()
        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            self.client.create_bucket(Bucket=self.bucket)

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def read_bytes(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def begin_multipart(self, key: str, content_type: str) -> str:
        result = self.client.create_multipart_upload(
            Bucket=self.bucket, Key=key, ContentType=content_type
        )
        return result["UploadId"]

    def put_part(self, key: str, upload_id: str, number: int, data: bytes) -> str:
        result = self.client.upload_part(
            Bucket=self.bucket, Key=key, UploadId=upload_id, PartNumber=number, Body=data
        )
        return result["ETag"].strip('"')

    def presign_part(self, key: str, upload_id: str, number: int) -> str | None:
        return self.client.generate_presigned_url(
            "upload_part",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "UploadId": upload_id,
                "PartNumber": number,
            },
            ExpiresIn=900,
        )

    def complete_multipart(
        self, key: str, upload_id: str, parts: list[dict[str, Any]], content_type: str
    ) -> None:
        normalized = [{"PartNumber": int(p["part_number"]), "ETag": p["etag"]} for p in parts]
        self.client.complete_multipart_upload(
            Bucket=self.bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={"Parts": normalized},
        )

    def presign_get(self, key: str, expires: int = 900) -> str | None:
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires
        )


@lru_cache
def get_storage() -> ObjectStorage:
    settings = get_settings()
    if settings.storage_backend == "s3":
        return S3Storage()
    return LocalStorage(settings.local_storage_path)
