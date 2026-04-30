import time
from sqlmodel import Session

from models.servicio import Servicio, ServicioCreate, ServicioPatch, ServicioRead
from repositories.servicio_repo import ServicioRepository
from services.minio_service import MinioService


class ServicioService:
    def __init__(self, session: Session) -> None:
        self._repo = ServicioRepository(session)
        self._minio = MinioService()

    def find_by_id(self, servicio_id: int) -> ServicioRead | None:
        s = self._repo.find_by_id(servicio_id)
        if s is None:
            return None
        return self._to_read(s)

    def find_all(self, page: int, size: int) -> tuple[list[ServicioRead], int]:
        offset = page * size
        items, total = self._repo.find_all(offset=offset, limit=size)
        return [self._to_read(s) for s in items], total

    def insert(self, data: ServicioCreate) -> int:
        s = self._repo.insert(data)
        return s.id  # type: ignore[return-value]

    def update(self, servicio_id: int, patch: ServicioPatch) -> bool:
        return self._repo.update(servicio_id, patch)

    def delete(self, servicio_id: int) -> bool:
        return self._repo.delete(servicio_id)

    def update_logo(self, servicio_id: int, file_data: bytes, original_filename: str) -> tuple[str, str | None]:
        old_servicio = self._repo.find_by_id(servicio_id)
        old_logo = old_servicio.logo if old_servicio else None

        ext = ""
        if "." in original_filename:
            ext = "." + original_filename.rsplit(".", 1)[-1]
        nombre = f"servicio_{servicio_id}_{int(time.time() * 1000)}{ext}"
        self._minio.upload(self._minio.BUCKET_SERVICIOS, nombre, file_data)
        self._repo.update_logo(servicio_id, nombre)

        if old_logo:
            self._minio.delete(self._minio.BUCKET_SERVICIOS, old_logo)
        url = self._minio.get_presigned_url(self._minio.BUCKET_SERVICIOS, nombre)
        return nombre, url

    def _to_read(self, s: Servicio) -> ServicioRead:
        url_logo = self._minio.get_presigned_url(self._minio.BUCKET_SERVICIOS, s.logo)
        return ServicioRead(id=s.id, nombre=s.nombre, url_logo=url_logo)  # type: ignore[arg-type]
