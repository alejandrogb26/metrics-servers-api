from __future__ import annotations

from sqlalchemy import delete, func, insert
from sqlmodel import Session, select

from models.servidor import Servidor, ServidorCreate, ServidorPatch, ServidorRead, ServidorServicio
from services.ssh_probe_service import ServidorInfo


class ServidorRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ── Consultas ──────────────────────────────────────────────────────────────

    def find_by_id(self, servidor_id: int) -> ServidorRead | None:
        srv = self.session.get(Servidor, servidor_id)
        if srv is None:
            return None
        servicios_map = self._get_servicio_ids([servidor_id])
        read = self._to_read(srv)
        read.servicios = servicios_map.get(servidor_id, [])
        return read

    def find_all(
        self, offset: int, limit: int, section_ids: set[int] | None = None
    ) -> tuple[list[ServidorRead], int]:
        # Guard: empty section set means no accessible sections → short-circuit.
        if section_ids is not None and not section_ids:
            return [], 0

        # COUNT(*) y SELECT LIMIT/OFFSET son dos queries separadas: un INSERT/DELETE
        # concurrente entre ellas puede hacer que `total` no coincida exactamente con
        # len(items). Trade-off aceptable para paginación de lectura sin escritura muy alta.
        count_stmt = select(func.count()).select_from(Servidor)
        data_stmt = select(Servidor).order_by(Servidor.id).offset(offset).limit(limit)
        if section_ids is not None:
            count_stmt = count_stmt.where(Servidor.seccion_id.in_(section_ids))
            data_stmt = data_stmt.where(Servidor.seccion_id.in_(section_ids))

        total: int = self.session.exec(count_stmt).one()
        items = list(self.session.exec(data_stmt).all())
        if not items:
            return [], total
        ids = [s.id for s in items]
        servicios_map = self._get_servicio_ids(ids)  # type: ignore[arg-type]
        result = []
        for s in items:
            read = self._to_read(s)
            read.servicios = servicios_map.get(s.id, [])  # type: ignore[arg-type]
            result.append(read)
        return result, total

    def find_server_id_by_id(self, servidor_id: int) -> str | None:
        srv = self.session.get(Servidor, servidor_id)
        return srv.server_id if srv else None

    def find_seccion_id_by_id(self, servidor_id: int) -> int | None:
        srv = self.session.get(Servidor, servidor_id)
        return srv.seccion_id if srv else None

    def find_seccion_id_by_server_id(self, server_id: str) -> int | None:
        srv = self.session.exec(
            select(Servidor).where(Servidor.server_id == server_id)
        ).first()
        return srv.seccion_id if srv else None

    def find_imagen_by_id(self, servidor_id: int) -> str | None:
        srv = self.session.get(Servidor, servidor_id)
        return srv.imagen if srv else None

    def exists(self, servidor_id: int) -> bool:
        return self.session.get(Servidor, servidor_id) is not None

    def exists_by_server_id(self, server_id: str) -> bool:
        return self.session.exec(
            select(Servidor).where(Servidor.server_id == server_id)
        ).first() is not None

    def find_by_ids(self, ids: list[int]) -> list[Servidor]:
        if not ids:
            return []
        return list(self.session.exec(select(Servidor).where(Servidor.id.in_(ids))).all())

    # ── Escritura ──────────────────────────────────────────────────────────────

    def insert(self, data: ServidorCreate, probe: ServidorInfo) -> int:
        try:
            srv = Servidor(
                server_id=data.server_id,
                dns=data.dns,
                seccion_id=data.seccion_id,
                hostname=probe.hostname,
                pretty_os=probe.pretty_os,
                arch=probe.arch,
                kernel=probe.kernel,
            )
            self.session.add(srv)
            self.session.flush()
            assert srv.id is not None
            if data.servicios:
                self._insert_servicios(srv.id, data.servicios)
            self.session.commit()
            return srv.id
        except Exception:
            self.session.rollback()
            raise

    def update(self, servidor_id: int, patch: ServidorPatch) -> bool:
        srv = self.session.get(Servidor, servidor_id)
        if srv is None:
            return False
        try:
            data = patch.model_dump(exclude_none=True, by_alias=False)
            for field, value in data.items():
                setattr(srv, field, value)
            self.session.add(srv)
            self.session.commit()
            return True
        except Exception:
            self.session.rollback()
            raise

    def delete(self, servidor_id: int) -> bool:
        srv = self.session.get(Servidor, servidor_id)
        if srv is None:
            return False
        try:
            self.session.delete(srv)
            self.session.commit()
            return True
        except Exception:
            self.session.rollback()
            raise

    def delete_bulk(self, ids: list[int]) -> int:
        if not ids:
            return 0
        try:
            result = self.session.execute(delete(Servidor).where(Servidor.id.in_(ids)))
            self.session.commit()
            return result.rowcount  # type: ignore[return-value]
        except Exception:
            self.session.rollback()
            raise

    def update_imagen(self, servidor_id: int, nombre_archivo: str) -> None:
        srv = self.session.get(Servidor, servidor_id)
        if srv is None:
            return
        try:
            srv.imagen = nombre_archivo
            self.session.add(srv)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    # ── Servicios asociados ────────────────────────────────────────────────────

    def add_servicios(self, servidor_id: int, servicio_ids: list[int]) -> int:
        if not servicio_ids:
            return 0
        try:
            self.session.execute(
                insert(ServidorServicio).prefix_with("IGNORE").values(
                    [{"servidor_id": servidor_id, "servicio_id": sid} for sid in servicio_ids]
                )
            )
            self.session.commit()
            return len(servicio_ids)
        except Exception:
            self.session.rollback()
            raise

    def remove_servicios(self, servidor_id: int, servicio_ids: list[int]) -> int:
        if not servicio_ids:
            return 0
        try:
            result = self.session.execute(
                delete(ServidorServicio).where(
                    ServidorServicio.servidor_id == servidor_id,
                    ServidorServicio.servicio_id.in_(servicio_ids),
                )
            )
            self.session.commit()
            return result.rowcount  # type: ignore[return-value]
        except Exception:
            self.session.rollback()
            raise

    # ── Helpers privados ───────────────────────────────────────────────────────

    def _insert_servicios(self, servidor_id: int, servicio_ids: list[int]) -> None:
        """Inserta asociaciones servidor↔servicio sin commit (llamar antes del commit del insert)."""
        self.session.execute(
            insert(ServidorServicio).prefix_with("IGNORE").values(
                [{"servidor_id": servidor_id, "servicio_id": sid} for sid in servicio_ids]
            )
        )

    def _get_servicio_ids(self, servidor_ids: list[int]) -> dict[int, list[int]]:
        """Carga los servicioId asociados a una lista de servidorId en una sola query."""
        if not servidor_ids:
            return {}
        rows = self.session.exec(
            select(ServidorServicio).where(ServidorServicio.servidor_id.in_(servidor_ids))
        ).all()
        result: dict[int, list[int]] = {}
        for row in rows:
            result.setdefault(row.servidor_id, []).append(row.servicio_id)
        return result

    @staticmethod
    def _to_read(srv: Servidor) -> ServidorRead:
        return ServidorRead(
            id=srv.id,  # type: ignore[arg-type]
            server_id=srv.server_id,
            dns=srv.dns,
            hostname=srv.hostname,
            pretty_os=srv.pretty_os,
            arch=srv.arch,
            kernel=srv.kernel,
            seccion_id=srv.seccion_id,
            imagen=srv.imagen,
            servicios=[],
        )
