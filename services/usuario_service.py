import time
from sqlmodel import Session

from repositories.usuario_repo import UsuarioRepository
from services.minio_service import MinioService


class UsuarioService:
    def __init__(self, session: Session) -> None:
        self._repo = UsuarioRepository(session)
        self._minio = MinioService()

    def update_foto_perfil(self, username: str, file_data: bytes, original_filename: str) -> tuple[str, str | None]:
        """Sube la foto a MinIO, actualiza la BD y devuelve (nombre_archivo, url_firmada)."""
        usuario = self._repo.find_by_username(username)
        old_foto = usuario.foto_perfil if usuario else None

        ext = ""
        if "." in original_filename:
            ext = "." + original_filename.rsplit(".", 1)[-1]
        nombre = f"user_{username}_{int(time.time() * 1000)}{ext}"
        self._minio.upload(self._minio.BUCKET_USERS, nombre, file_data)
        self._repo.update_foto(username, nombre)

        if old_foto:
            self._minio.delete(self._minio.BUCKET_USERS, old_foto)
        url = self._minio.get_presigned_url(self._minio.BUCKET_USERS, nombre)
        return nombre, url

    def get_url_foto(self, nombre_archivo: str | None) -> str | None:
        return self._minio.get_presigned_url(self._minio.BUCKET_USERS, nombre_archivo)
