"""
Servicio de aplicación para la entidad UsuarioApp.

Capa arquitectónica: Aplicación / Servicio.

Responsabilidades:
    - Gestionar el ciclo de vida de la foto de perfil de un usuario: generar un
      nombre único, subir el fichero a MinIO, actualizar la BD y eliminar la
      foto anterior (`update_foto_perfil`).
    - Generar URLs presignadas de acceso temporal a fotos de perfil almacenadas
      en MinIO (`get_url_foto`).

Qué NO debe contener este fichero:
    - Lógica de autenticación ni de sesión. Eso pertenece a `auth_service.py`.
    - Creación ni sincronización de usuarios. Los usuarios se crean y sincronizan
      en `AuthService._sync_usuario_app`, no aquí.
    - Acceso directo a la base de datos. Toda operación de BD pasa por
      `UsuarioRepository`.
    - Lógica HTTP ni manejo de excepciones HTTP. Eso pertenece a
      `routers/usuario.py`.

Nomenclatura de objetos en MinIO:
    Las fotos de perfil se almacenan en `BUCKET_USERS` con el patrón:
        `user_{username}_{timestamp_ms}{extension}`
    El timestamp en milisegundos garantiza unicidad ante subidas rápidas
    consecutivas del mismo usuario. La extensión se extrae del nombre
    original del fichero.

Relaciones con otros módulos:
    - `models/usuario.py`            → `UsuarioApp` (ORM), implícito via repo.
    - `repositories/usuario_repo.py` → `UsuarioRepository` para lectura y
                                       actualización de `foto_perfil`.
    - `services/minio_service.py`    → `MinioService` para subida, URL y borrado
                                       de fotos.
    - `services/auth_service.py`     → llama a `get_url_foto` en el login para
                                       incluir la URL de la foto en el JWT payload.
    - `routers/usuario.py`           → instancia `UsuarioService(session)` en el
                                       endpoint de subida de foto.

Autor:
    Alejandro Gómez Blanco

Proyecto:
    Metrics Servers

Versión:
    1.0.0

Organización:
    Metrics Servers Project
"""

import time
from sqlmodel import Session

from repositories.usuario_repo import UsuarioRepository
from services.minio_service import MinioService


class UsuarioService:
    """
    Servicio de gestión de foto de perfil para la entidad UsuarioApp.

    Combina `UsuarioRepository` y `MinioService` para proporcionar las
    operaciones de foto de perfil. Es el servicio más pequeño del proyecto
    en cuanto a responsabilidades: solo gestiona un campo de la entidad usuario
    (`foto_perfil`) sin intervenir en el ciclo de vida del usuario en sí.
    """

    def __init__(self, session: Session) -> None:
        self._repo = UsuarioRepository(session)
        self._minio = MinioService()

    def update_foto_perfil(
        self, username: str, file_data: bytes, original_filename: str
    ) -> tuple[str, str | None]:
        """
        Sube la foto de perfil a MinIO, actualiza la BD y devuelve el nombre
        del fichero y la URL presignada.

        Flujo:
            1. Recupera el nombre de la foto actual (`old_foto`) para poder
               eliminarla al final. Si el usuario no existe en BD, `old_foto`
               es `None` y el borrado se omite.
            2. Genera un nombre de fichero único con el patrón
               `user_{username}_{timestamp_ms}{ext}`. La extensión se extrae
               del nombre original del fichero (todo lo que hay tras el último
               `.`). Si el fichero no tiene extensión, se usa cadena vacía.
            3. Sube el nuevo fichero a `BUCKET_USERS`.
            4. Actualiza la columna `foto_perfil` en la BD mediante
               `UsuarioRepository.update_foto`.
            5. Elimina la foto anterior de MinIO (best-effort: si falla, el
               error se silencia en `MinioService.delete`).
            6. Genera la URL presignada del nuevo fichero y la devuelve junto
               con el nombre.

        Inconsistencia de atomicidad:
            Los pasos 3 (MinIO) y 4 (BD) son independientes. Si el paso 3
            tiene éxito pero el paso 4 falla, el fichero queda huérfano en
            MinIO. A diferencia de `ServidorService.update_foto`, no hay
            compensación (no se borra el fichero de MinIO si la BD falla).

        Args:
            username:          Nombre de usuario que identifica al `UsuarioApp`
                               en BD y forma parte del nombre del fichero.
            file_data:         Contenido del fichero en bytes.
            original_filename: Nombre original del fichero subido, usado solo
                               para extraer la extensión.

        Retorna:
            Tupla `(nombre_archivo, url)` donde `nombre_archivo` es la clave
            del objeto en MinIO y `url` es la URL presignada (puede ser `None`
            si `get_presigned_url` falla).
        """
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
        """
        Genera una URL presignada de acceso temporal a una foto de perfil.

        Delega directamente en `MinioService.get_presigned_url`, que devuelve
        `None` si `nombre_archivo` es falsy (usuario sin foto) o si MinIO falla.
        Se usa en `AuthService._sync_usuario_app` durante el login para incluir
        la URL de la foto en el payload del JWT de respuesta.

        Args:
            nombre_archivo: Clave del objeto en MinIO (valor de `foto_perfil`
                            en BD), o `None` si el usuario no tiene foto.

        Retorna:
            URL presignada como string, o `None` si no hay foto o si MinIO no
            puede generar la URL.
        """
        return self._minio.get_presigned_url(self._minio.BUCKET_USERS, nombre_archivo)
