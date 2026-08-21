import io
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import BinaryIO

import boto3

from app.core.config import get_settings


class Storage:
    def put_fileobj(self, key: str, fileobj: BinaryIO, content_type: str | None = None) -> None:
        raise NotImplementedError

    def put_bytes(self, key: str, content: bytes, content_type: str | None = None) -> None:
        self.put_fileobj(key, io.BytesIO(content), content_type)

    def read_bytes(self, key: str) -> bytes:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError

    def presigned_url(self, key: str, download_name: str | None = None) -> str | None:
        return None

    @contextmanager
    def materialize(self, key: str, suffix: str = "") -> Iterator[Path]:
        with NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(self.read_bytes(key))
            path = Path(tmp.name)
        try:
            yield path
        finally:
            path.unlink(missing_ok=True)


class LocalStorage(Storage):
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValueError("非法存储路径")
        return path

    def put_fileobj(self, key: str, fileobj: BinaryIO, content_type: str | None = None) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as output:
            shutil.copyfileobj(fileobj, output)

    def read_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def local_path(self, key: str) -> Path:
        return self._path(key)

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class S3Storage(Storage):
    def __init__(self):
        settings = get_settings()
        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            self.client.create_bucket(Bucket=self.bucket)

    def put_fileobj(self, key: str, fileobj: BinaryIO, content_type: str | None = None) -> None:
        extra = {"ContentType": content_type} if content_type else None
        self.client.upload_fileobj(fileobj, self.bucket, key, ExtraArgs=extra or {})

    def read_bytes(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def presigned_url(self, key: str, download_name: str | None = None) -> str:
        params = {"Bucket": self.bucket, "Key": key}
        if download_name:
            params["ResponseContentDisposition"] = f'attachment; filename="{download_name}"'
        return self.client.generate_presigned_url("get_object", Params=params, ExpiresIn=300)


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        settings = get_settings()
        _storage = (
            S3Storage() if settings.storage_backend == "s3" else LocalStorage(settings.storage_root)
        )
    return _storage


def reset_storage() -> None:
    global _storage
    _storage = None
