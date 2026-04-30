"""
Servicio de gestión de servidores.
Equivalente a ServidorService.java.
"""

import concurrent.futures
import time
from sqlmodel import Session

from core.mongo import get_mongo_db
from models.common import BulkResult
from models.servidor import ServidorCreate, ServidorPatch, ServidorPatchRequest, ServidorRead
from repositories.mongo_repo import MongoRepository
from repositories.servidor_repo import ServidorRepository
from services.minio_service import MinioService
from services.ssh_probe_service import ServidorInfo, SshProbeService


class ServidorService:
    def __init__(self, session: Session) -> None:
        self._repo = ServidorRepository(session)
        self._mongo = MongoRepository(get_mongo_db())
        self._minio = MinioService()
        self._probe = SshProbeService()

    # ── Consultas ──────────────────────────────────────────────────────────────

    def find_by_id(self, servidor_id: int) -> ServidorRead | None:
        servidor = self._repo.find_by_id(servidor_id)
        if servidor:
            self._resolve_imagen_url(servidor)
        return servidor

    def find_all(self, page: int, size: int) -> tuple[list[ServidorRead], int]:
        offset = page * size
        servidores, total = self._repo.find_all(offset=offset, limit=size)
        for s in servidores:
            self._resolve_imagen_url(s)
        return servidores, total

    # ── Creación ───────────────────────────────────────────────────────────────

    def insert(self, dto: ServidorCreate) -> ServidorRead:
        # Insert first: if DB raises IntegrityError (duplicate), no SSH time wasted.
        srv_id = self._repo.insert(dto)
        try:
            info = self._probe.ask_server(dto.dns)
            if info:
                self._repo.update(srv_id, ServidorPatch(
                    hostname=info.hostname,
                    pretty_os=info.pretty_os,
                    arch=info.arch,
                    kernel=info.kernel,
                ))
        except Exception:
            pass
        servidor = self._repo.find_by_id(srv_id)
        self._resolve_imagen_url(servidor)
        return servidor

    # ── Creación en lote ───────────────────────────────────────────────────────

    # Número máximo de conexiones SSH simultáneas durante el bulk create.
    _MAX_PROBE_WORKERS = 10

    def insert_bulk(self, items: list[ServidorCreate]) -> BulkResult:
        # Fase 1: todos los probes SSH en paralelo.
        # Cada probe es I/O bloqueante (hasta TIMEOUT s); ejecutarlos en serie daría
        # N×TIMEOUT en el peor caso. Con ThreadPoolExecutor el total es ~1×TIMEOUT
        # independientemente de cuántos servidores sean inalcanzables.
        probe_map = self._probe_all(items)

        # Fase 2: inserts secuenciales en la BD (el driver SQL no es thread-safe).
        result = BulkResult(total=len(items))
        for dto in items:
            try:
                srv_id = self._repo.insert(dto)
                info = probe_map.get(dto.dns)
                if info:
                    self._repo.update(srv_id, ServidorPatch(
                        hostname=info.hostname,
                        pretty_os=info.pretty_os,
                        arch=info.arch,
                        kernel=info.kernel,
                    ))
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
                except Exception:
                    probe_map[dns] = None
        return probe_map

    # ── Modificación ───────────────────────────────────────────────────────────

    def update(self, servidor_id: int, patch: ServidorPatchRequest) -> bool:
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

    def delete(self, servidor_id: int) -> bool:
        server_id = self._repo.find_server_id_by_id(servidor_id)
        deleted = self._repo.delete(servidor_id)
        if deleted and server_id:
            self._mongo.delete_by_server_id(server_id)
        return deleted

    def delete_bulk(self, ids: list[int]) -> BulkResult:
        found = self._repo.find_by_ids(ids)
        found_id_set = {s.id for s in found}
        missing = [i for i in ids if i not in found_id_set]

        deleted_count = self._repo.delete_bulk(ids)
        for s in found:
            self._mongo.delete_by_server_id(s.server_id)

        errors = [f"ID {i} no encontrado" for i in missing]
        return BulkResult(total=len(ids), ok=deleted_count, failed=len(missing), errors=errors)

    # ── Servicios asociados ────────────────────────────────────────────────────

    def add_servicios(self, servidor_id: int, servicio_ids: list[int]) -> int | None:
        if not self._repo.exists(servidor_id):
            return None
        return self._repo.add_servicios(servidor_id, servicio_ids)

    def remove_servicios(self, servidor_id: int, servicio_ids: list[int]) -> int | None:
        if not self._repo.exists(servidor_id):
            return None
        return self._repo.remove_servicios(servidor_id, servicio_ids)

    # ── Foto ───────────────────────────────────────────────────────────────────

    def update_foto(self, servidor_id: int, file_data: bytes, original_filename: str) -> str:
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

    def get_metrics(self, server_id: str, minutes: int = 60) -> list[dict] | None:
        if not self._repo.exists_by_server_id(server_id):
            return None
        return self._mongo.get_metrics(server_id, minutes)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _resolve_imagen_url(self, servidor: ServidorRead) -> None:
        # presigned_get_object genera la URL localmente mediante HMAC-SHA256 sobre las
        # credenciales de acceso. No realiza ninguna llamada de red a MinIO, por lo que
        # llamarlo una vez por servidor en un listado paginado no supone latencia de I/O.
        servidor.imagen_url = self._minio.get_presigned_url(
            self._minio.BUCKET_SERVIDORES, servidor.imagen
        )
