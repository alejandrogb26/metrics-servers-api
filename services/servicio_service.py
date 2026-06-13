"""
Servicio de aplicación para la entidad Servicio.

Capa arquitectónica: Aplicación / Servicio.

Responsabilidades:
    - Coordinar el CRUD de servicios delegando en `ServicioRepository`.
    - Convertir objetos ORM `Servicio` a DTOs `ServicioRead` resolviendo la
      URL presignada del logo desde MinIO (`_to_read`).
    - Gestionar el ciclo de vida completo del logo: generar un nombre de
      fichero único, subir el nuevo fichero a MinIO, actualizar la BD y
      eliminar el fichero anterior (`update_logo`).

Qué NO debe contener este fichero:
    - Acceso directo a la base de datos. Toda operación de BD pasa por
      `ServicioRepository`.
    - Lógica HTTP ni manejo de excepciones HTTP. Eso pertenece a
      `routers/servicio.py`.
    - Operaciones de bajo nivel con MinIO. Eso pertenece a
      `services/minio_service.py`.

Nomenclatura de objetos en MinIO:
    Los logos se almacenan en `BUCKET_SERVICIOS` con el patrón:
        `servicio_{id}_{timestamp_ms}{extension}`
    El timestamp en milisegundos garantiza unicidad ante subidas rápidas
    consecutivas del mismo servicio. La extensión se extrae del nombre
    original del fichero.

Relaciones con otros módulos:
    - `models/servicio.py`          → `Servicio` (ORM), `ServicioCreate`,
                                      `ServicioPatch`, `ServicioRead`.
    - `repositories/servicio_repo.py` → `ServicioRepository` para todas las
                                        operaciones de BD.
    - `services/minio_service.py`   → `MinioService` para subida, URL y borrado
                                      de logos.
    - `routers/servicio.py`         → instancia `ServicioService(session)` en
                                      cada handler.

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

from exceptions.errors import NotFoundException
from models.servicio import Servicio, ServicioCreate, ServicioPatch, ServicioRead
from repositories.servicio_repo import ServicioRepository
from services.minio_service import MinioService


class ServicioService:
    """
    Servicio CRUD para la entidad Servicio con gestión de logos en MinIO.

    Combina el repositorio relacional con `MinioService` para proporcionar
    operaciones completas sobre servicios. La transformación ORM→DTO se
    centraliza en `_to_read`, que resuelve la URL presignada del logo en
    cada conversión.
    """

    def __init__(self, session: Session) -> None:
        self._repo = ServicioRepository(session)
        self._minio = MinioService()

    def find_by_id(self, servicio_id: int) -> ServicioRead:
        """
        Busca un servicio por clave primaria y lo devuelve con la URL del logo.

        Args:
            servicio_id: Clave primaria del servicio.

        Retorna:
            `ServicioRead` con `url_logo` resuelta desde MinIO.

        Lanza:
            `NotFoundException` si no existe un servicio con `servicio_id`.
        """
        s = self._repo.find_by_id(servicio_id)
        if s is None:
            raise NotFoundException(f"Servicio con id={servicio_id} no encontrado")
        return self._to_read(s)

    def find_all(self, page: int, size: int) -> tuple[list[ServicioRead], int]:
        """
        Devuelve una página de servicios con sus URLs de logo y el total.

        Convierte `page`/`size` a `offset`/`limit` y mapea cada ORM a DTO
        mediante `_to_read`. Cada conversión genera una llamada a MinIO para
        obtener la URL presignada del logo. Con N servicios por página se
        emiten N llamadas a MinIO.

        Args:
            page: Número de página, base 0.
            size: Número máximo de elementos por página.

        Retorna:
            Tupla `(lista_de_ServicioRead, total_sin_paginar)`.
        """
        offset = page * size
        items, total = self._repo.find_all(offset=offset, limit=size)
        return [self._to_read(s) for s in items], total

    def insert(self, data: ServicioCreate) -> int:
        """
        Crea un nuevo servicio sin logo y devuelve el ID asignado.

        El logo se gestiona por separado mediante `update_logo`. El servicio
        se crea únicamente con `nombre`; el campo `logo` queda `None` hasta
        que se suba el fichero correspondiente.

        Args:
            data: DTO `ServicioCreate` con el `nombre` del servicio.

        Retorna:
            `id` auto-incremental asignado al servicio recién creado.
        """
        s = self._repo.insert(data)
        return s.id  # type: ignore[return-value]

    def update(self, servicio_id: int, patch: ServicioPatch) -> None:
        """
        Actualiza los campos editables de un servicio (PATCH semántico).

        Solo `nombre` forma parte de `ServicioPatch`; el campo `logo` no puede
        modificarse por esta vía (solo a través de `update_logo`).

        Args:
            servicio_id: ID del servicio a actualizar.
            patch:       DTO `ServicioPatch` con los campos a modificar.

        Lanza:
            `NotFoundException` si no existe un servicio con `servicio_id`.
        """
        if not self._repo.update(servicio_id, patch):
            raise NotFoundException(f"Servicio con id={servicio_id} no encontrado")

    def delete(self, servicio_id: int) -> None:
        """
        Elimina un servicio por clave primaria.

        Solo elimina el registro en MariaDB. El fichero de logo en MinIO no
        se elimina automáticamente; queda como objeto huérfano en el bucket.

        Args:
            servicio_id: ID del servicio a eliminar.

        Lanza:
            `NotFoundException` si no existe un servicio con `servicio_id`.
        """
        if not self._repo.delete(servicio_id):
            raise NotFoundException(f"Servicio con id={servicio_id} no encontrado")

    def update_logo(self, servicio_id: int, file_data: bytes, original_filename: str) -> tuple[str, str | None]:
        """
        Sube un logo nuevo a MinIO, actualiza la BD y elimina el logo anterior.

        Flujo completo de la operación:
            1. Recupera el nombre del logo actual (`old_logo`) para poder
               eliminarlo al final. Si el servicio no existe, `old_logo` es
               `None` y el borrado se omite.
            2. Genera un nombre de fichero único con el patrón
               `servicio_{id}_{timestamp_ms}{ext}`, donde el timestamp en
               milisegundos garantiza unicidad ante subidas consecutivas
               rápidas del mismo servicio. La extensión se extrae del nombre
               original del fichero (todo lo que hay tras el último `.`).
            3. Sube el nuevo fichero a `BUCKET_SERVICIOS` con el nombre
               generado.
            4. Persiste el nuevo nombre en la columna `logo` de la BD mediante
               `ServicioRepository.update_logo`.
            5. Elimina el logo anterior de MinIO (best-effort: si falla, el
               error se silencia en `MinioService.delete`).
            6. Genera y devuelve la URL presignada del nuevo logo.

        Inconsistencia de atomicidad:
            Los pasos 3 (MinIO) y 4 (BD) son operaciones independientes. Si
            el paso 3 tiene éxito pero el paso 4 falla, el fichero queda en
            MinIO sin referencia en BD (huérfano). Si el paso 4 tiene éxito
            pero el paso 3 falló, la BD apunta a un fichero inexistente en
            MinIO.

        Args:
            servicio_id:       ID del servicio cuyo logo se actualiza.
            file_data:         Contenido del fichero en bytes.
            original_filename: Nombre original del fichero, usado solo para
                               extraer la extensión.

        Retorna:
            Tupla `(nombre_archivo, url)` donde `nombre_archivo` es la clave
            del objeto en MinIO y `url` es la URL presignada (o `None` si
            `get_presigned_url` falla).
        """
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
        """
        Convierte un ORM `Servicio` en `ServicioRead` resolviendo la URL del logo.

        Llama a `MinioService.get_presigned_url` para generar la URL de acceso
        temporal al logo. Si `s.logo` es `None` (sin logo asignado) o si MinIO
        falla, `url_logo` queda `None` en el DTO devuelto.

        Centraliza la construcción del DTO para que `find_by_id` y `find_all`
        produzcan exactamente el mismo formato de salida.

        Args:
            s: Objeto ORM `Servicio` ya cargado desde la BD.

        Retorna:
            `ServicioRead` con `id`, `nombre` y `url_logo` (puede ser `None`).
        """
        url_logo = self._minio.get_presigned_url(self._minio.BUCKET_SERVICIOS, s.logo)
        return ServicioRead(id=s.id, nombre=s.nombre, url_logo=url_logo)  # type: ignore[arg-type]
