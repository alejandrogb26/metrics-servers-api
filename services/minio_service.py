"""
Servicio de almacenamiento de objetos con MinIO.
Equivalente a MinioService.java.
"""

import io
from datetime import timedelta

from minio.error import S3Error

from core.config import get_settings
from core.minio_client import get_minio_client


class MinioService:
    def __init__(self) -> None:
        self._client = get_minio_client()
        s = get_settings()
        self.BUCKET_USERS = s.bucket_users
        self.BUCKET_SERVIDORES = s.bucket_servidores
        self.BUCKET_SERVICIOS = s.bucket_servicios

    def upload(self, bucket: str, object_name: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self._ensure_bucket(bucket)
        self._client.put_object(
            bucket_name=bucket,
            object_name=object_name,
            data=io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )

    def get_presigned_url(self, bucket: str, object_name: str | None, expires: int = 3600) -> str | None:
        if not object_name:
            return None
        try:
            return self._client.presigned_get_object(
                bucket_name=bucket,
                object_name=object_name,
                expires=timedelta(seconds=expires),
            )
        except S3Error:
            return None

    def delete(self, bucket: str, object_name: str) -> None:
        try:
            self._client.remove_object(bucket, object_name)
        except S3Error:
            pass

    def _ensure_bucket(self, bucket: str) -> None:
        if not self._client.bucket_exists(bucket):
            self._client.make_bucket(bucket)
