from sqlmodel import Session

from models.common import BulkResult
from models.grupo import GrupoCreate, GrupoPatch, GrupoRead
from models.permission_map import PermissionMap
from repositories.grupo_repo import GrupoRepository
from services.ldap_service import LdapService


class GrupoService:
    def __init__(self, session: Session) -> None:
        self._repo = GrupoRepository(session)
        self._ldap = LdapService()

    def get_all(self, page: int, size: int) -> tuple[list[GrupoRead], int]:
        offset = page * size
        return self._repo.find_all(offset=offset, limit=size)

    def get_by_id(self, grupo_id: int) -> GrupoRead | None:
        return self._repo.find_by_id(grupo_id)

    def create_bulk(self, items: list[GrupoCreate]) -> BulkResult:
        result = BulkResult(total=len(items))
        for data in items:
            try:
                # Punto 2 de Java: verificar que el DN existe en AD
                if data.dn and not self._ldap.dn_exists(data.dn):
                    raise ValueError(f"El DN '{data.dn}' no existe en Active Directory")
                self._repo.insert(data)
                result.ok += 1
            except Exception as exc:
                result.failed += 1
                result.errors.append(f"{data.nombre}: {exc}")
        return result

    def patch(self, grupo_id: int, patch: GrupoPatch) -> bool:
        return self._repo.update(grupo_id, patch)

    def patch_superadmin(self, grupo_id: int, superadmin: bool) -> bool:
        return self._repo.update_superadmin(grupo_id, superadmin)

    def delete(self, grupo_id: int) -> bool:
        return self._repo.delete(grupo_id)

    def delete_bulk(self, ids: list[int]) -> BulkResult:
        deleted = self._repo.delete_bulk(ids)
        failed = len(ids) - deleted
        return BulkResult(total=len(ids), ok=deleted, failed=failed)


class GrupoPermisosService:
    def __init__(self, session: Session) -> None:
        self._repo = GrupoRepository(session)

    def replace_all(self, grupo_id: int, permisos: PermissionMap[int]) -> bool:
        return self._repo.replace_all_permissions(grupo_id, permisos)

    def patch_global(
        self, grupo_id: int, to_add: list[int] | None, to_remove: list[int] | None
    ) -> bool:
        return self._repo.patch_global_permissions(grupo_id, to_add, to_remove)

    def replace_seccion(self, grupo_id: int, seccion_id: int, permiso_ids: list[int]) -> bool:
        return self._repo.replace_section_permissions(grupo_id, seccion_id, permiso_ids)

    def patch_seccion(
        self, grupo_id: int, seccion_id: int, to_add: list[int] | None, to_remove: list[int] | None
    ) -> bool:
        return self._repo.patch_section_permissions(grupo_id, seccion_id, to_add, to_remove)
