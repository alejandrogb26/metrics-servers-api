"""
Servicio de almacenamiento de objetos con MinIO.
Equivalente a MinioService.java.
"""

import io
import logging
from datetime import timedelta

from minio.error import S3Error

from core.config import get_settings
from core.minio_client import get_minio_client

log = logging.getLogger("api.minio")


class MinioService:
    def __init__(self) -> None:
        self._client = get_minio_client()
        s = get_settings()
        self.BUCKET_USERS = s.bucket_users
        self.BUCKET_SERVIDORES = s.bucket_servidores
        self.BUCKET_SERVICIOS = s.bucket_servicios

    def upload(self, bucket: str, object_name: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        log.debug("MINIO upload bucket=%s key=%s size=%d type=%s",
                  bucket, object_name, len(data), content_type)
        self._ensure_bucket(bucket)
        self._client.put_object(
            bucket_name=bucket,
            object_name=object_name,
            data=io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )
        log.debug("MINIO upload ok bucket=%s key=%s", bucket, object_name)

    def get_presigned_url(self, bucket: str, object_name: str | None, expires: int = 3600) -> str | None:
        if not object_name:
            log.debug("MINIO presigned skip: object_name vacío bucket=%s", bucket)
            return None
        log.debug("MINIO presigned bucket=%s key=%s expires=%d", bucket, object_name, expires)
        try:
            url = self._client.presigned_get_object(
                bucket_name=bucket,
                object_name=object_name,
                expires=timedelta(seconds=expires),
            )
            log.debug("MINIO presigned ok bucket=%s key=%s", bucket, object_name)
            return url
        except S3Error as exc:
            log.debug("MINIO presigned error bucket=%s key=%s: %s", bucket, object_name, exc)
            return None

    def delete(self, bucket: str, object_name: str) -> None:
        log.debug("MINIO delete bucket=%s key=%s", bucket, object_name)
        try:
            self._client.remove_object(bucket, object_name)
            log.debug("MINIO delete ok bucket=%s key=%s", bucket, object_name)
        except S3Error as exc:
            log.debug("MINIO delete error bucket=%s key=%s: %s", bucket, object_name, exc)

    def _ensure_bucket(self, bucket: str) -> None:
        exists = self._client.bucket_exists(bucket)
        if not exists:
            log.debug("MINIO ensure_bucket creando bucket=%s", bucket)
            self._client.make_bucket(bucket)
        else:
            log.debug("MINIO ensure_bucket existe bucket=%s", bucket)
