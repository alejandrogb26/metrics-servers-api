from sqlalchemy import delete, func, insert
from sqlmodel import Session, select

from models.ambito import Ambito
from models.grupo import Grupo, GrupoCreate, GrupoPatch, GrupoPermisoGlobal, GrupoRead, GrupoSeccion
from models.permiso import Permiso
from models.permission_map import PermissionMap


class GrupoRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ── Lecturas ───────────────────────────────────────────────────────────────

    def find_by_id(self, grupo_id: int) -> GrupoRead | None:
        grupo = self.session.get(Grupo, grupo_id)
        if grupo is None:
            return None
        return self._to_read(grupo)

    def find_all(self, offset: int, limit: int) -> tuple[list[GrupoRead], int]:
        # COUNT(*) y SELECT LIMIT/OFFSET son dos queries separadas: un INSERT/DELETE
        # concurrente entre ellas puede hacer que `total` no coincida exactamente con
        # len(items). Trade-off aceptable para paginación de lectura sin escritura muy alta.
        total: int = self.session.exec(select(func.count()).select_from(Grupo)).one()
        grupos = list(self.session.exec(select(Grupo).offset(offset).limit(limit)).all())
        if not grupos:
            return [], total

        grupo_ids = [g.id for g in grupos]
        global_map = self._get_all_global_perm_ids(grupo_ids)  # type: ignore[arg-type]
        section_map = self._get_all_section_perm_ids(grupo_ids)  # type: ignore[arg-type]

        result = [
            GrupoRead(
                id=g.id,
                nombre=g.nombre,
                dn=g.dn,
                superadmin=g.superadmin,
                permisos=PermissionMap[int](
                    global_perms=global_map.get(g.id, []),  # type: ignore[arg-type]
                    sections=section_map.get(g.id, {}),  # type: ignore[arg-type]
                ),
            )
            for g in grupos
        ]
        return result, total

    def find_by_any_dn(self, dns: list[str]) -> Grupo | None:
        """Devuelve el grupo cuyo DN coincida con alguno de la lista."""
        if not dns:
            return None
        stmt = select(Grupo).where(Grupo.dn.in_(dns))  # type: ignore[union-attr]
        results = list(self.session.exec(stmt).all())
        if not results:
            return None
        if len(results) > 1:
            raise ValueError("El usuario pertenece a más de un grupo válido de la aplicación")
        return results[0]

    # ── Escritura ──────────────────────────────────────────────────────────────

    def insert(self, data: GrupoCreate) -> int:
        try:
            grupo = Grupo(
                nombre=data.nombre,
                dn=data.dn,
                superadmin=data.superadmin or False,
            )
            self.session.add(grupo)
            self.session.flush()
            assert grupo.id is not None

            if data.permisos:
                self._save_permissions(grupo.id, data.permisos)

            self.session.commit()
            return grupo.id
        except Exception:
            self.session.rollback()
            raise

    def update(self, grupo_id: int, patch: GrupoPatch) -> bool:
        grupo = self.session.get(Grupo, grupo_id)
        if grupo is None:
            return False
        try:
            if patch.nombre is not None:
                grupo.nombre = patch.nombre
            self.session.add(grupo)
            self.session.commit()
            return True
        except Exception:
            self.session.rollback()
            raise

    def update_superadmin(self, grupo_id: int, superadmin: bool) -> bool:
        grupo = self.session.get(Grupo, grupo_id)
        if grupo is None:
            return False
        try:
            grupo.superadmin = superadmin
            self.session.add(grupo)
            self.session.commit()
            return True
        except Exception:
            self.session.rollback()
            raise

    def delete(self, grupo_id: int) -> bool:
        grupo = self.session.get(Grupo, grupo_id)
        if grupo is None:
            return False
        try:
            self.session.delete(grupo)
            self.session.commit()
            return True
        except Exception:
            self.session.rollback()
            raise

    def delete_bulk(self, ids: list[int]) -> int:
        if not ids:
            return 0
        try:
            result = self.session.execute(delete(Grupo).where(Grupo.id.in_(ids)))
            self.session.commit()
            return result.rowcount  # type: ignore[return-value]
        except Exception:
            self.session.rollback()
            raise

    # ── Permisos ───────────────────────────────────────────────────────────────

    def get_global_permission_names(self, grupo_id: int) -> list[str]:
        stmt = (
            select(Permiso.nombre, Ambito.nombre)
            .join(GrupoPermisoGlobal, GrupoPermisoGlobal.permiso_id == Permiso.id)
            .join(Ambito, Ambito.id == Permiso.ambito_id)
            .where(GrupoPermisoGlobal.grupo_id == grupo_id)
        )
        rows = self.session.exec(stmt).all()  # type: ignore[call-overload]
        return [f"{row[0]}_{row[1]}" for row in rows]

    def get_section_permission_names(self, grupo_id: int) -> dict[int, list[str]]:
        stmt = (
            select(GrupoSeccion.seccion_id, Permiso.nombre, Ambito.nombre)
            .join(Permiso, Permiso.id == GrupoSeccion.permiso_id)
            .join(Ambito, Ambito.id == Permiso.ambito_id)
            .where(GrupoSeccion.grupo_id == grupo_id)
        )
        rows = self.session.exec(stmt).all()  # type: ignore[call-overload]
        result: dict[int, list[str]] = {}
        for row in rows:
            result.setdefault(row[0], []).append(f"{row[1]}_{row[2]}")
        return result

    def get_global_permission_ids(self, grupo_id: int) -> list[int]:
        rows = self.session.exec(
            select(GrupoPermisoGlobal).where(GrupoPermisoGlobal.grupo_id == grupo_id)
        ).all()
        return [r.permiso_id for r in rows]

    def get_section_permission_ids(self, grupo_id: int) -> dict[int, list[int]]:
        rows = self.session.exec(
            select(GrupoSeccion).where(GrupoSeccion.grupo_id == grupo_id)
        ).all()
        result: dict[int, list[int]] = {}
        for row in rows:
            result.setdefault(row.seccion_id, []).append(row.permiso_id)
        return result

    def replace_all_permissions(self, grupo_id: int, permisos: PermissionMap[int]) -> bool:
        if not self.session.get(Grupo, grupo_id):
            return False
        try:
            self._delete_all_permissions(grupo_id)
            self._save_permissions(grupo_id, permisos)
            self.session.commit()
            return True
        except Exception:
            self.session.rollback()
            raise

    def patch_global_permissions(
        self, grupo_id: int, to_add: list[int] | None, to_remove: list[int] | None
    ) -> bool:
        if not self.session.get(Grupo, grupo_id):
            return False
        try:
            if to_remove:
                self.session.execute(
                    delete(GrupoPermisoGlobal).where(
                        GrupoPermisoGlobal.grupo_id == grupo_id,
                        GrupoPermisoGlobal.permiso_id.in_(to_remove),
                    )
                )
            if to_add:
                self.session.execute(
                    insert(GrupoPermisoGlobal).prefix_with("IGNORE").values(
                        [{"grupo_id": grupo_id, "permiso_id": pid} for pid in to_add]
                    )
                )
            self.session.commit()
            return True
        except Exception:
            self.session.rollback()
            raise

    def replace_section_permissions(
        self, grupo_id: int, seccion_id: int, permiso_ids: list[int]
    ) -> bool:
        if not self.session.get(Grupo, grupo_id):
            return False
        try:
            self.session.execute(
                delete(GrupoSeccion).where(
                    GrupoSeccion.grupo_id == grupo_id,
                    GrupoSeccion.seccion_id == seccion_id,
                )
            )
            if permiso_ids:
                self.session.execute(
                    insert(GrupoSeccion).prefix_with("IGNORE").values(
                        [
                            {"grupo_id": grupo_id, "seccion_id": seccion_id, "permiso_id": pid}
                            for pid in permiso_ids
                        ]
                    )
                )
            self.session.commit()
            return True
        except Exception:
            self.session.rollback()
            raise

    def patch_section_permissions(
        self, grupo_id: int, seccion_id: int, to_add: list[int] | None, to_remove: list[int] | None
    ) -> bool:
        if not self.session.get(Grupo, grupo_id):
            return False
        try:
            if to_remove:
                self.session.execute(
                    delete(GrupoSeccion).where(
                        GrupoSeccion.grupo_id == grupo_id,
                        GrupoSeccion.seccion_id == seccion_id,
                        GrupoSeccion.permiso_id.in_(to_remove),
                    )
                )
            if to_add:
                self.session.execute(
                    insert(GrupoSeccion).prefix_with("IGNORE").values(
                        [
                            {"grupo_id": grupo_id, "seccion_id": seccion_id, "permiso_id": pid}
                            for pid in to_add
                        ]
                    )
                )
            self.session.commit()
            return True
        except Exception:
            self.session.rollback()
            raise

    # ── Helpers privados ───────────────────────────────────────────────────────

    def _to_read(self, grupo: Grupo) -> GrupoRead:
        pmap = PermissionMap[int](
            global_perms=self.get_global_permission_ids(grupo.id),  # type: ignore[arg-type]
            sections=self.get_section_permission_ids(grupo.id),  # type: ignore[arg-type]
        )
        return GrupoRead(
            id=grupo.id,  # type: ignore[arg-type]
            nombre=grupo.nombre,
            dn=grupo.dn,
            superadmin=grupo.superadmin,
            permisos=pmap,
        )

    def _delete_all_permissions(self, grupo_id: int) -> None:
        self.session.execute(
            delete(GrupoPermisoGlobal).where(GrupoPermisoGlobal.grupo_id == grupo_id)
        )
        self.session.execute(
            delete(GrupoSeccion).where(GrupoSeccion.grupo_id == grupo_id)
        )

    def _save_permissions(self, grupo_id: int, pmap: PermissionMap[int]) -> None:
        if pmap.global_perms:
            self.session.execute(
                insert(GrupoPermisoGlobal).prefix_with("IGNORE").values(
                    [{"grupo_id": grupo_id, "permiso_id": pid} for pid in pmap.global_perms]
                )
            )
        if pmap.sections:
            rows = [
                {"grupo_id": grupo_id, "seccion_id": int(sid), "permiso_id": pid}
                for sid, pids in pmap.sections.items()
                for pid in pids
            ]
            if rows:
                self.session.execute(
                    insert(GrupoSeccion).prefix_with("IGNORE").values(rows)
                )

    def _get_all_global_perm_ids(self, grupo_ids: list[int]) -> dict[int, list[int]]:
        if not grupo_ids:
            return {}
        rows = self.session.exec(
            select(GrupoPermisoGlobal).where(GrupoPermisoGlobal.grupo_id.in_(grupo_ids))
        ).all()
        result: dict[int, list[int]] = {}
        for row in rows:
            result.setdefault(row.grupo_id, []).append(row.permiso_id)
        return result

    def _get_all_section_perm_ids(self, grupo_ids: list[int]) -> dict[int, dict[int, list[int]]]:
        if not grupo_ids:
            return {}
        rows = self.session.exec(
            select(GrupoSeccion).where(GrupoSeccion.grupo_id.in_(grupo_ids))
        ).all()
        result: dict[int, dict[int, list[int]]] = {}
        for row in rows:
            result.setdefault(row.grupo_id, {}).setdefault(row.seccion_id, []).append(row.permiso_id)
        return result
