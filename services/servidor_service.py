"""
Servicio de gestión de servidores.
Equivalente a ServidorService.java.

Capa arquitectónica: Aplicación / Servicio.

Responsabilidades:
    - Orquestar las operaciones CRUD sobre servidores coordinando cuatro
      sistemas de infraestructura: MariaDB (`ServidorRepository`), MongoDB
      (`MongoRepository`), MinIO (`MinioService`) y SSH (`SshProbeService`).
    - Aplicar el filtro de visibilidad por sección en todos los métodos que
      reciben `section_ids`: `None` para superadmin (sin filtro), `set[int]`
      para usuarios con acceso restringido.
    - Ejecutar el probe SSH antes de insertar o al actualizar el DNS, para
      poblar los campos de diagnóstico del servidor (hostname, OS, arch, kernel).
    - Mantener la consistencia entre MariaDB y MongoDB al renombrar (`server_id`)
      o eliminar servidores.
    - Gestionar el ciclo de vida de las imágenes de servidor en MinIO.

Qué NO debe contener este fichero:
    - Acceso directo a bases de datos ni a MinIO. Todo pasa por los repositorios
      y servicios de infraestructura correspondientes.
    - Lógica HTTP. Eso pertenece a `routers/servidor.py`.
    - Lógica de conexión SSH. Eso pertenece a `services/ssh_probe_service.py`.

Filtro de visibilidad por sección (`section_ids`):
    Todos los métodos mutantes y de lectura reciben `section_ids: set[int] | None`.
    - `None`          → superadmin; sin restricción de sección.
    - `set[int]`      → el servidor debe pertenecer a una de las secciones del
                        conjunto. Si no pertenece, el método devuelve `None` o
                        `False`, indistinguible de "no encontrado", para no
                        revelar la existencia de recursos no autorizados.

Relaciones con otros módulos:
    - `core/mongo.py`                  → `get_mongo_db` para instanciar
                                         `MongoRepository`.
    - `exceptions/errors.py`           → `ProbeException` (HTTP 502) cuando el
                                         probe SSH falla en un insert individual.
    - `models/common.py`               → `BulkResult`.
    - `models/servidor.py`             → `ServidorCreate`, `ServidorPatch`,
                                         `ServidorPatchRequest`, `ServidorRead`.
    - `repositories/mongo_repo.py`     → `MongoRepository` para métricas.
    - `repositories/servidor_repo.py`  → `ServidorRepository` para MariaDB.
    - `services/minio_service.py`      → `MinioService` para imágenes.
    - `services/ssh_probe_service.py`  → `SshProbeService` y `ServidorInfo`
                                         para el probe SSH.
    - `routers/servidor.py`            → instancia `ServidorService(session)`.

Autor:
    Alejandro Gómez Blanco

Proyecto:
    Metrics Servers

Versión:
    1.0.0

Organización:
    Metrics Servers Project
"""

import concurrent.futures
import logging
import time
from sqlmodel import Session

from core.mongo import get_mongo_db
from exceptions.errors import ProbeException
from models.common import BulkResult
from models.servidor import ServidorCreate, ServidorPatch, ServidorPatchRequest, ServidorRead
from repositories.mongo_repo import MongoRepository
from repositories.servidor_repo import ServidorRepository
from services.minio_service import MinioService
from services.ssh_probe_service import ServidorInfo, SshProbeService

_log = logging.getLogger(__name__)


class ServidorService:
    """
    Servicio central para la gestión de servidores.

    Coordina los cuatro sistemas de infraestructura del dominio de servidores.
    Todas las instancias de repositorios y servicios de infraestructura se
    crean en el constructor; los repositorios comparten la sesión de BD recibida.
    """

    def __init__(self, session: Session) -> None:
        self._repo = ServidorRepository(session)
        self._mongo = MongoRepository(get_mongo_db())
        self._minio = MinioService()
        self._probe = SshProbeService()

    # ── Consultas ──────────────────────────────────────────────────────────────

    def find_by_id(
        self, servidor_id: int, section_ids: set[int] | None = None
    ) -> ServidorRead | None:
        """
        Devuelve un servidor por PK si es visible para el usuario.

        Carga el servidor desde el repositorio y aplica el filtro de sección
        en Python (no en SQL). Si el servidor existe pero su `seccion_id` no
        está en `section_ids`, devuelve `None` indistinguible de "no encontrado"
        para no revelar la existencia de recursos no autorizados.

        Tras la comprobación de visibilidad, resuelve `imagen_url` mediante
        `_resolve_imagen_url`.

        Args:
            servidor_id: PK del servidor en MariaDB.
            section_ids: Secciones visibles para el usuario, o `None` si
                         es superadmin.

        Retorna:
            `ServidorRead` con `imagen_url` resuelta, o `None` si no existe
            o no es visible.
        """
        servidor = self._repo.find_by_id(servidor_id)
        if servidor is None:
            return None
        if section_ids is not None and servidor.seccion_id not in section_ids:
            return None  # treated as not-found to avoid revealing existence
        self._resolve_imagen_url(servidor)
        return servidor

    def find_all(
        self, page: int, size: int, section_ids: set[int] | None = None
    ) -> tuple[list[ServidorRead], int]:
        """
        Devuelve una página de servidores visibles para el usuario.

        El filtro de sección se aplica en SQL (a diferencia de `find_by_id`,
        que lo aplica en Python). El repositorio recibe `section_ids` y lo
        incorpora al `WHERE IN` de la query, lo que es más eficiente que
        cargar todos los servidores y filtrar en memoria.

        Tras la carga, resuelve `imagen_url` para cada servidor mediante
        `_resolve_imagen_url`. Dado que `presigned_get_object` opera localmente
        (HMAC-SHA256 sin llamada de red), el coste es despreciable.

        Args:
            page:        Número de página, base 0.
            size:        Número máximo de elementos por página.
            section_ids: Secciones visibles, o `None` para superadmin.

        Retorna:
            Tupla `(lista_de_ServidorRead, total_sin_paginar)`.
        """
        offset = page * size
        servidores, total = self._repo.find_all(
            offset=offset, limit=size, section_ids=section_ids
        )
        for s in servidores:
            self._resolve_imagen_url(s)
        return servidores, total

    # ── Creación ───────────────────────────────────────────────────────────────

    def insert(self, dto: ServidorCreate) -> ServidorRead:
        """
        Crea un servidor ejecutando primero el probe SSH para obtener sus datos.

        El probe SSH es obligatorio para la creación individual: si falla,
        eleva `ProbeException` (HTTP 502) y aborta sin insertar nada en BD.
        Los datos del probe (`ServidorInfo`) se combinan con los datos del
        usuario (`ServidorCreate`) en una sola inserción atómica.

        Tras el insert, carga el `ServidorRead` completo mediante `find_by_id`
        para incluir los servicios asociados y la `imagen_url`.

        Args:
            dto: `ServidorCreate` con los datos del usuario (dns, server_id,
                 seccion_id, servicios iniciales).

        Retorna:
            `ServidorRead` completo del servidor recién creado.

        Lanza:
            `ProbeException` si el probe SSH falla (cualquier excepción del
            probe se envuelve y re-lanza).
        """
        try:
            info = self._probe.ask_server(dto.dns)
        except Exception as exc:
            _log.warning("SSH probe fallido para '%s' (%s): %s", dto.server_id, dto.dns, exc)
            raise ProbeException(
                f"No se pudieron obtener datos SSH obligatorios para '{dto.server_id}'"
                " (hostname/prettyOs/arch/kernel)"
            ) from exc
        _log.debug(
            "SSH probe OK para '%s': hostname=%s os=%s arch=%s kernel=%s",
            dto.server_id, info.hostname, info.pretty_os, info.arch, info.kernel,
        )
        srv_id = self._repo.insert(dto, info)
        servidor = self._repo.find_by_id(srv_id)
        self._resolve_imagen_url(servidor)
        return servidor

    # ── Creación en lote ───────────────────────────────────────────────────────

    # Número máximo de conexiones SSH simultáneas durante el bulk create.
    _MAX_PROBE_WORKERS = 10

    def insert_bulk(self, items: list[ServidorCreate]) -> BulkResult:
        """
        Crea múltiples servidores con probes SSH paralelos e inserts secuenciales.

        Implementa un procesamiento en dos fases para maximizar el rendimiento
        y respetar las restricciones del driver de BD:

        Fase 1 — Probes SSH en paralelo (`_probe_all`):
            Todos los probes se ejecutan concurrentemente con un
            `ThreadPoolExecutor` de hasta `_MAX_PROBE_WORKERS` hilos. Un probe
            SSH puede bloquearse hasta TIMEOUT segundos si el host no responde;
            ejecutarlos en serie daría N×TIMEOUT en el peor caso. Con el pool,
            el tiempo total es ~1×TIMEOUT independientemente del número de
            servidores inalcanzables. Los resultados se indexan por `dns`
            (`probe_map: dict[str, ServidorInfo | None]`).

        Fase 2 — Inserts secuenciales en BD:
            El driver SQL no es thread-safe; los inserts se hacen de uno en
            uno. Un probe fallido (`probe_map[dto.dns] is None`) acumula el
            fallo en `result.errors` y continúa con el siguiente elemento sin
            abortar el bulk.

        Args:
            items: Lista de `ServidorCreate` a insertar.

        Retorna:
            `BulkResult` con `total`, `ok` (insertados), `failed` (fallos de
            probe o de BD) y `errors` (mensajes por elemento fallido).
        """
        # Fase 1: todos los probes SSH en paralelo.
        # Cada probe es I/O bloqueante (hasta TIMEOUT s); ejecutarlos en serie daría
        # N×TIMEOUT en el peor caso. Con ThreadPoolExecutor el total es ~1×TIMEOUT
        # independientemente de cuántos servidores sean inalcanzables.
        probe_map = self._probe_all(items)

        # Fase 2: inserts secuenciales en la BD (el driver SQL no es thread-safe).
        result = BulkResult(total=len(items))
        for dto in items:
            info = probe_map.get(dto.dns)
            _log.debug("SSH probe para '%s': %s", dto.server_id, "OK" if info else "FALLIDO")
            if info is None:
                result.failed += 1
                result.errors.append(
                    f"{dto.server_id}: no se pudieron obtener datos SSH obligatorios"
                    " (hostname/prettyOs/arch/kernel)"
                )
                continue
            try:
                self._repo.insert(dto, info)
                result.ok += 1
            except Exception as exc:
                result.failed += 1
                result.errors.append(f"{dto.server_id}: {exc}")
        return result

    def _probe_all(self, items: list[ServidorCreate]) -> dict[str, ServidorInfo | None]:
        """Ejecuta SSH probes en paralelo. Devuelve dns → ServidorInfo (o None si falla)."""
        workers = min(len(items), self._MAX_PROBE_WORKERS)
        probe_map: dict[str, ServidorInfo | None] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self._probe.ask_server, dto.dns): dto.dns for dto in items}
            for future in concurrent.futures.as_completed(futures):
                dns = futures[future]
                try:
                    probe_map[dns] = future.result()
                except Exception as exc:
                    _log.debug("SSH probe fallido para %s: %s", dns, exc)
                    probe_map[dns] = None
        return probe_map

    # ── Modificación ───────────────────────────────────────────────────────────

    def update(
        self, servidor_id: int, patch: ServidorPatchRequest, section_ids: set[int] | None = None
    ) -> bool:
        """
        Actualiza un servidor con validaciones de sección y sincronización de
        MongoDB y SSH.

        Validación de sección (si `section_ids` no es None):
            1. La sección ACTUAL del servidor debe estar en `section_ids`. Si no,
               devuelve `False` (tratado como no encontrado).
            2. Si el patch incluye `seccion_id`, la sección DESTINO también debe
               estar en `section_ids`. Esto impide mover un servidor a una sección
               no accesible para el usuario.

        Sincronización de MongoDB:
            Si `server_id` cambia, se actualiza el campo en todos los documentos
            de MongoDB para mantener la consistencia con las métricas históricas.
            La operación se realiza solo si el update en MariaDB tuvo éxito y el
            nuevo `server_id` es diferente al anterior.

        Re-probe SSH al cambiar DNS:
            Si el patch incluye un nuevo `dns`, se ejecuta un probe SSH para
            actualizar hostname/OS/arch/kernel. A diferencia del insert (donde el
            fallo del probe es fatal), aquí el fallo se silencia con
            `except Exception: pass` sin ningún log. El servidor queda con los
            datos de diagnóstico del DNS anterior.

        Traducción de DTO:
            `ServidorPatchRequest` (DTO público con alias camelCase) se traduce a
            `ServidorPatch` (DTO interno con nombres ORM snake_case) antes de
            llamar al repositorio.

        Args:
            servidor_id: PK del servidor a actualizar.
            patch:       DTO `ServidorPatchRequest` con los campos a modificar.
            section_ids: Secciones visibles, o `None` para superadmin.

        Retorna:
            `True` si el servidor existía y fue actualizado; `False` si no
            existe o no es visible para el usuario.
        """
        if section_ids is not None:
            current_seccion = self._repo.find_seccion_id_by_id(servidor_id)
            if current_seccion is None or current_seccion not in section_ids:
                return False
            # Prevent moving a server to a section the caller cannot access.
            if patch.seccion_id is not None and patch.seccion_id not in section_ids:
                return False

        old_server_id: str | None = None
        if patch.server_id is not None:
            old_server_id = self._repo.find_server_id_by_id(servidor_id)

        db_patch = ServidorPatch(
            server_id=patch.server_id,
            dns=patch.dns,
            seccion_id=patch.seccion_id,
        )
        updated = self._repo.update(servidor_id, db_patch)

        if updated:
            if old_server_id and patch.server_id and old_server_id != patch.server_id:
                self._mongo.update_server_id(old_server_id, patch.server_id)

            if patch.dns is not None:
                try:
                    info = self._probe.ask_server(patch.dns)
                    if info:
                        self._repo.update(servidor_id, ServidorPatch(
                            hostname=info.hostname,
                            pretty_os=info.pretty_os,
                            arch=info.arch,
                            kernel=info.kernel,
                        ))
                except Exception:
                    pass

        return updated

    def delete(self, servidor_id: int, section_ids: set[int] | None = None) -> bool:
        """
        Elimina un servidor de MariaDB y limpia sus métricas en MongoDB.

        Aplica el filtro de sección antes de eliminar. Recupera el `server_id`
        antes del delete porque tras él el registro ya no existe y no puede
        consultarse. La limpieza de MongoDB solo se ejecuta si el delete en
        MariaDB tuvo éxito y se conoce el `server_id`.

        Args:
            servidor_id: PK del servidor a eliminar.
            section_ids: Secciones visibles, o `None` para superadmin.

        Retorna:
            `True` si el servidor existía y fue eliminado; `False` si no
            existe o no es visible.
        """
        if section_ids is not None:
            current_seccion = self._repo.find_seccion_id_by_id(servidor_id)
            if current_seccion is None or current_seccion not in section_ids:
                return False
        server_id = self._repo.find_server_id_by_id(servidor_id)
        deleted = self._repo.delete(servidor_id)
        if deleted and server_id:
            self._mongo.delete_by_server_id(server_id)
        return deleted

    def delete_bulk(self, ids: list[int], section_ids: set[int] | None = None) -> BulkResult:
        """
        Elimina múltiples servidores con filtro de sección y limpieza de MongoDB.

        Pre-carga todos los servidores en una sola query batch (`find_by_ids`)
        para evitar N queries individuales. Luego los clasifica en tres grupos:
          - `not_found`:    IDs que no existen en BD.
          - `accessible`:   IDs existentes y visibles para el usuario.
          - `not_authorized`: IDs existentes pero en secciones no accesibles.

        Los servidores `not_authorized` se tratan como `not_found` en el
        `BulkResult` (mensaje "ID X no encontrado") para no revelar la
        existencia de recursos no autorizados.

        El delete SQL se emite como un único `DELETE WHERE IN` sobre los IDs
        accesibles. La limpieza de MongoDB se hace en un bucle (una llamada
        por servidor), no en batch.

        Args:
            ids:         Lista de PKs a eliminar.
            section_ids: Secciones visibles, o `None` para superadmin.

        Retorna:
            `BulkResult` con `total`, `ok` (eliminados), `failed` (no
            encontrados + no autorizados) y `errors` con mensajes por ID.
        """
        found = self._repo.find_by_ids(ids)
        found_map = {s.id: s for s in found}

        not_found = [i for i in ids if i not in found_map]

        if section_ids is not None:
            accessible = [s for s in found if s.seccion_id in section_ids]
            # Inaccessible servers are reported as not-found (no 403 leak).
            not_authorized = [s.id for s in found if s.seccion_id not in section_ids]
        else:
            accessible = found
            not_authorized = []

        accessible_ids = [s.id for s in accessible]
        deleted_count = self._repo.delete_bulk(accessible_ids) if accessible_ids else 0
        for s in accessible:
            self._mongo.delete_by_server_id(s.server_id)

        failed_ids = not_found + not_authorized
        errors = [f"ID {i} no encontrado" for i in failed_ids]
        return BulkResult(
            total=len(ids), ok=deleted_count, failed=len(failed_ids), errors=errors
        )

    # ── Servicios asociados ────────────────────────────────────────────────────

    def add_servicios(
        self, servidor_id: int, servicio_ids: list[int], section_ids: set[int] | None = None
    ) -> int | None:
        """
        Asocia servicios a un servidor, con filtro de sección.

        La comprobación de visibilidad varía según `section_ids`:
          - Si no es `None`: busca la sección actual del servidor y verifica
            que está en `section_ids`. Más preciso que `exists()`.
          - Si es `None` (superadmin): comprueba solo existencia con
            `self._repo.exists(servidor_id)`.

        Devuelve `None` para señalizar "servidor no encontrado o no accesible"
        al router, que lo convierte en `HTTP 404`.

        Args:
            servidor_id:  PK del servidor.
            servicio_ids: IDs de servicios a asociar.
            section_ids:  Secciones visibles, o `None` para superadmin.

        Retorna:
            Número de IDs enviados (no de filas insertadas, por `INSERT IGNORE`),
            o `None` si el servidor no existe o no es accesible.
        """
        if section_ids is not None:
            current_seccion = self._repo.find_seccion_id_by_id(servidor_id)
            if current_seccion is None or current_seccion not in section_ids:
                return None
        elif not self._repo.exists(servidor_id):
            return None
        return self._repo.add_servicios(servidor_id, servicio_ids)

    def remove_servicios(
        self, servidor_id: int, servicio_ids: list[int], section_ids: set[int] | None = None
    ) -> int | None:
        """
        Desasocia servicios de un servidor, con filtro de sección.

        Mismo patrón de visibilidad que `add_servicios`. Devuelve el número
        de filas realmente eliminadas de `servidores_servicios`, o `None` si
        el servidor no existe o no es accesible.

        Args:
            servidor_id:  PK del servidor.
            servicio_ids: IDs de servicios a desasociar.
            section_ids:  Secciones visibles, o `None` para superadmin.

        Retorna:
            Número de asociaciones eliminadas, o `None` si el servidor no
            existe o no es accesible.
        """
        if section_ids is not None:
            current_seccion = self._repo.find_seccion_id_by_id(servidor_id)
            if current_seccion is None or current_seccion not in section_ids:
                return None
        elif not self._repo.exists(servidor_id):
            return None
        return self._repo.remove_servicios(servidor_id, servicio_ids)

    # ── Foto ───────────────────────────────────────────────────────────────────

    def update_foto(self, servidor_id: int, file_data: bytes, original_filename: str) -> str:
        """
        Sube una imagen nueva a MinIO, actualiza la BD y elimina la imagen anterior.

        A diferencia de `ServicioService.update_logo`, este método implementa
        una compensación parcial: si el update en BD falla tras haber subido el
        fichero a MinIO, elimina el fichero recién subido y re-lanza la excepción.
        Esto evita dejar ficheros huérfanos en MinIO cuando la BD falla.

        Flujo:
            1. Recupera el nombre de la imagen actual (`old_imagen`) para su
               posterior borrado.
            2. Genera un nombre único: `server_{id}_{timestamp_ms}{ext}`.
            3. Sube el nuevo fichero a `BUCKET_SERVIDORES`.
            4. Actualiza la BD. Si falla, elimina el fichero del paso 3 y
               re-lanza la excepción (compensación).
            5. Elimina la imagen anterior de MinIO (best-effort).
            6. Devuelve el nombre del fichero almacenado.

        A diferencia de `ServicioService.update_logo`, devuelve solo `nombre`
        (no una tupla `(nombre, url)`). El router construye `UploadResult` con
        `url_foto=None`.

        Args:
            servidor_id:       PK del servidor.
            file_data:         Contenido del fichero en bytes.
            original_filename: Nombre original, usado solo para extraer la
                               extensión.

        Retorna:
            Nombre del fichero almacenado en MinIO.

        Lanza:
            Cualquier excepción de `update_imagen` del repositorio, tras
            eliminar el fichero de MinIO como compensación.
        """
        old_imagen = self._repo.find_imagen_by_id(servidor_id)

        ext = ""
        if "." in original_filename:
            ext = "." + original_filename.rsplit(".", 1)[-1]
        nombre = f"server_{servidor_id}_{int(time.time() * 1000)}{ext}"
        self._minio.upload(self._minio.BUCKET_SERVIDORES, nombre, file_data)
        try:
            self._repo.update_imagen(servidor_id, nombre)
        except Exception:
            self._minio.delete(self._minio.BUCKET_SERVIDORES, nombre)
            raise

        if old_imagen:
            self._minio.delete(self._minio.BUCKET_SERVIDORES, old_imagen)
        return nombre

    # ── Métricas ───────────────────────────────────────────────────────────────

    def get_metrics(
        self, server_id: str, minutes: int = 60, section_ids: set[int] | None = None
    ) -> list[dict] | None:
        """
        Devuelve métricas de MongoDB para un servidor, con filtro de sección.

        El parámetro es `server_id: str` (ID externo del agente), no
        `servidor_id: int` (PK de MariaDB). Para aplicar el filtro de sección,
        primero resuelve la sección del servidor en MariaDB usando el
        `server_id` externo, y solo si es visible consulta MongoDB.

        Tres valores de retorno con semántica distinta:
          - `None`      → servidor no existe en MariaDB o no es visible.
          - `[]`        → servidor existe y es visible, pero sin métricas en
                          la ventana de tiempo solicitada.
          - `list[dict]`→ lista de documentos de métricas en orden cronológico.

        Args:
            server_id:   Identificador externo del servidor (string).
            minutes:     Ventana de tiempo hacia atrás en minutos. Por defecto 60.
            section_ids: Secciones visibles, o `None` para superadmin.

        Retorna:
            Lista de documentos dict, lista vacía, o `None` según lo indicado.
        """
        seccion_id = self._repo.find_seccion_id_by_server_id(server_id)
        if seccion_id is None:
            return None
        if section_ids is not None and seccion_id not in section_ids:
            return None  # treated as not-found
        return self._mongo.get_metrics(server_id, minutes)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _resolve_imagen_url(self, servidor: ServidorRead) -> None:
        # presigned_get_object genera la URL localmente mediante HMAC-SHA256 sobre las
        # credenciales de acceso. No realiza ninguna llamada de red a MinIO, por lo que
        # llamarlo una vez por servidor en un listado paginado no supone latencia de I/O.
        servidor.imagen_url = self._minio.get_presigned_url(
            self._minio.BUCKET_SERVIDORES, servidor.imagen
        )
