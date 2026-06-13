"""
Servicios de aplicación para la entidad Grupo y sus permisos.

Capa arquitectónica: Aplicación / Servicio.

Este módulo contiene dos clases de servicio con responsabilidades separadas,
en correspondencia con la división del router (`routers/grupo.py` y
`routers/grupo_permisos.py`):

  - `GrupoService`         → CRUD del grupo + validación de DN contra AD.
  - `GrupoPermisosService` → gestión de los permisos del grupo (delegación pura).

Responsabilidades de `GrupoService`:
    - Coordinar la creación, actualización, lectura y borrado de grupos.
    - Validar que el DN de grupo existe en Active Directory antes de persistirlo
      (regla de negocio: un grupo sin DN válido en AD no tiene sentido en el
      sistema).
    - Acumular resultados de operaciones bulk sin abortar ante fallos parciales.

Responsabilidades de `GrupoPermisosService`:
    - Delegar las operaciones de reemplazo e incremento de permisos (globales y
      por sección) en `GrupoRepository`. No añade lógica adicional.

Qué NO deben contener estos ficheros:
    - Acceso directo a la base de datos. Toda operación de BD pasa por
      `GrupoRepository`.
    - Lógica HTTP ni manejo de excepciones HTTP. Los `ValueError` lanzados aquí
      son capturados por el router y convertidos a `HTTP 422`.
    - Operaciones de binding LDAP. Eso pertenece a `services/ldap_service.py`.

Relaciones con otros módulos:
    - `models/common.py`          → `BulkResult` para operaciones batch.
    - `models/grupo.py`           → `GrupoCreate`, `GrupoPatch`, `GrupoRead`.
    - `models/permission_map.py`  → `PermissionMap[int]` para `replace_all`.
    - `repositories/grupo_repo.py`→ `GrupoRepository` para todas las operaciones de BD.
    - `services/ldap_service.py`  → `LdapService.dn_exists` para validar DNs en AD.
    - `routers/grupo.py`          → instancia `GrupoService(session)`.
    - `routers/grupo_permisos.py` → instancia `GrupoPermisosService(session)`.

Autor:
    Alejandro Gómez Blanco

Proyecto:
    Metrics Servers

Versión:
    1.0.0

Organización:
    Metrics Servers Project
"""

from sqlmodel import Session

from exceptions.errors import NotFoundException, ValidationException
from models.common import BulkResult
from models.grupo import GrupoCreate, GrupoPatch, GrupoRead
from models.permission_map import PermissionMap
from repositories.grupo_repo import GrupoRepository
from services.ldap_service import LdapService


class GrupoService:
    """
    Servicio CRUD para grupos con validación de DN en Active Directory.

    Crea `GrupoRepository` y `LdapService` en el constructor. `LdapService`
    es necesario porque la regla de negocio principal de este servicio es
    que cualquier DN asociado a un grupo debe existir en AD antes de persistirlo.
    """

    def __init__(self, session: Session) -> None:
        self._repo = GrupoRepository(session)
        self._ldap = LdapService()

    def get_all(self, page: int, size: int) -> tuple[list[GrupoRead], int]:
        """
        Devuelve una página de grupos con sus permisos completos.

        Delega directamente en el repositorio, que ya devuelve `GrupoRead`
        (con permisos globales y por sección cargados mediante batch queries).

        Args:
            page: Número de página, base 0.
            size: Número máximo de elementos por página.

        Retorna:
            Tupla `(lista_de_GrupoRead, total_sin_paginar)`.
        """
        offset = page * size
        return self._repo.find_all(offset=offset, limit=size)

    def get_by_id(self, grupo_id: int) -> GrupoRead:
        """
        Devuelve un grupo con sus permisos por clave primaria.

        Args:
            grupo_id: Clave primaria del grupo.

        Retorna:
            `GrupoRead` completo (con permisos).

        Lanza:
            `NotFoundException` si no existe un grupo con `grupo_id`.
        """
        grupo = self._repo.find_by_id(grupo_id)
        if grupo is None:
            raise NotFoundException(f"Grupo con id={grupo_id} no encontrado")
        return grupo

    def create_bulk(self, items: list[GrupoCreate]) -> BulkResult:
        """
        Crea uno o varios grupos de forma tolerante a fallos parciales.

        Procesa cada `GrupoCreate` de forma independiente. Si un elemento falla
        (validación de DN o error de BD), el error se acumula en `result.errors`
        y el procesamiento continúa con el siguiente elemento. Esto garantiza
        que un fallo individual no aborta toda la operación bulk.

        Validación de DN (regla de negocio — "Punto 2 de Java"):
            Si `data.dn` tiene valor, verifica mediante `LdapService.dn_exists`
            que el DN existe en Active Directory. Si no existe, lanza `ValueError`
            que se captura como fallo de ese elemento. Si `data.dn` es `None` o
            vacío, la verificación se omite (el DN es opcional en el modelo).

        La captura usa `except Exception` (genérico) para que cualquier tipo de
        fallo (ValueError de validación, IntegrityError de BD, error LDAP) se
        trate como fallo del elemento sin detener el bulk.

        Args:
            items: Lista de `GrupoCreate` a insertar.

        Retorna:
            `BulkResult` con `total` (len(items)), `ok` (insertados) y `failed`
            (fallidos), más la lista `errors` con el mensaje de cada fallo.
        """
        result = BulkResult(total=len(items))
        for data in items:
            try:
                # Punto 2 de Java: verificar que el DN existe en AD
                if data.dn and not self._ldap.dn_exists(data.dn):
                    raise ValidationException(f"El DN '{data.dn}' no existe en Active Directory")
                self._repo.insert(data)
                result.ok += 1
            except Exception as exc:
                result.failed += 1
                result.errors.append(f"{data.nombre}: {exc}")
        return result

    def patch(self, grupo_id: int, patch: GrupoPatch) -> None:
        """
        Actualiza los campos editables de un grupo (PATCH semántico).

        Validación de DN en PATCH:
            Solo valida el DN en AD si se cumplen dos condiciones simultáneas:
            1. El campo `dn` fue incluido explícitamente en el body (detectado
               mediante `model_fields_set`).
            2. El valor enviado es no-falsy (no es `None` ni cadena vacía).

            Si se envía `{"dn": null}` o `{"dn": ""}`, la verificación LDAP
            se omite. Si se envía un DN con valor, se verifica en AD.

        Args:
            grupo_id: ID del grupo a actualizar.
            patch:    DTO `GrupoPatch` con los campos a modificar.

        Lanza:
            `NotFoundException`    si no existe un grupo con `grupo_id`.
            `ValidationException`  si el DN enviado no existe en Active Directory.
        """
        if "dn" in patch.model_fields_set and patch.dn:
            if not self._ldap.dn_exists(patch.dn):
                raise ValidationException(f"El DN '{patch.dn}' no existe en Active Directory")
        if not self._repo.update(grupo_id, patch):
            raise NotFoundException(f"Grupo con id={grupo_id} no encontrado")

    def patch_superadmin(self, grupo_id: int, superadmin: bool) -> None:
        """
        Cambia el flag `superadmin` de un grupo.

        Operación sensible de seguridad: delegada directamente al repositorio
        sin validación LDAP (el flag superadmin es un concepto propio de la
        aplicación, no de AD).

        Args:
            grupo_id:   ID del grupo a modificar.
            superadmin: Nuevo valor del flag (`True` o `False`).

        Lanza:
            `NotFoundException` si no existe un grupo con `grupo_id`.
        """
        if not self._repo.update_superadmin(grupo_id, superadmin):
            raise NotFoundException(f"Grupo con id={grupo_id} no encontrado")

    def delete(self, grupo_id: int) -> None:
        """
        Elimina un grupo por clave primaria.

        Args:
            grupo_id: ID del grupo a eliminar.

        Lanza:
            `NotFoundException` si no existe un grupo con `grupo_id`.
        """
        if not self._repo.delete(grupo_id):
            raise NotFoundException(f"Grupo con id={grupo_id} no encontrado")

    def delete_bulk(self, ids: list[int]) -> BulkResult:
        """
        Elimina múltiples grupos en una sola operación SQL.

        A diferencia de `create_bulk` (que procesa elemento a elemento),
        `delete_bulk` emite un único `DELETE WHERE IN` mediante el repositorio.
        Los IDs que no corresponden a grupos existentes se contabilizan como
        fallos sin lanzar excepción.

        Args:
            ids: Lista de PKs de grupos a eliminar.

        Retorna:
            `BulkResult` con `total` (len(ids)), `ok` (eliminados efectivamente)
            y `failed` (IDs no encontrados = `total - ok`). El campo `errors`
            queda vacío porque el borrado bulk no produce mensajes de error
            por elemento.
        """
        deleted = self._repo.delete_bulk(ids)
        failed = len(ids) - deleted
        return BulkResult(total=len(ids), ok=deleted, failed=failed)


class GrupoPermisosService:
    """
    Servicio de delegación pura para la gestión de permisos de grupos.

    No añade lógica de negocio más allá de lo que ofrece `GrupoRepository`.
    Existe para mantener la simetría arquitectónica con el router
    `routers/grupo_permisos.py` y para que ese router no dependa directamente
    del repositorio.

    Todos los métodos devuelven `bool`: `True` si el grupo existe y la
    operación se realizó; `False` si el grupo no fue encontrado.
    """

    def __init__(self, session: Session) -> None:
        self._repo = GrupoRepository(session)

    def replace_all(self, grupo_id: int, permisos: PermissionMap[int]) -> None:
        """
        Reemplaza todos los permisos del grupo (globales + todas las secciones).

        Operación destructiva: elimina el conjunto completo de permisos actuales
        y los sustituye por los proporcionados en `permisos`.

        Args:
            grupo_id: ID del grupo.
            permisos: `PermissionMap[int]` con los nuevos permisos globales
                      y por sección.

        Lanza:
            `NotFoundException` si no existe un grupo con `grupo_id`.
        """
        if not self._repo.replace_all_permissions(grupo_id, permisos):
            raise NotFoundException(f"Grupo con id={grupo_id} no encontrado")

    def patch_global(
        self, grupo_id: int, to_add: list[int] | None, to_remove: list[int] | None
    ) -> None:
        """
        Modifica permisos globales del grupo de forma incremental.

        Args:
            grupo_id:  ID del grupo.
            to_add:    IDs de permisos a añadir, o `None` para no añadir.
            to_remove: IDs de permisos a eliminar, o `None` para no eliminar.

        Lanza:
            `NotFoundException` si no existe un grupo con `grupo_id`.
        """
        if not self._repo.patch_global_permissions(grupo_id, to_add, to_remove):
            raise NotFoundException(f"Grupo con id={grupo_id} no encontrado")

    def replace_seccion(self, grupo_id: int, seccion_id: int, permiso_ids: list[int]) -> None:
        """
        Reemplaza todos los permisos de una sección concreta para el grupo.

        Args:
            grupo_id:    ID del grupo.
            seccion_id:  ID de la sección.
            permiso_ids: Lista de IDs de permiso que deben quedar asignados.
                         Lista vacía elimina todos los permisos de esa sección.

        Lanza:
            `NotFoundException` si no existe un grupo con `grupo_id`.
        """
        if not self._repo.replace_section_permissions(grupo_id, seccion_id, permiso_ids):
            raise NotFoundException(f"Grupo con id={grupo_id} no encontrado")

    def patch_seccion(
        self, grupo_id: int, seccion_id: int, to_add: list[int] | None, to_remove: list[int] | None
    ) -> None:
        """
        Modifica permisos de una sección concreta para el grupo de forma incremental.

        Args:
            grupo_id:   ID del grupo.
            seccion_id: ID de la sección.
            to_add:     IDs de permisos a añadir, o `None` para no añadir.
            to_remove:  IDs de permisos a eliminar, o `None` para no eliminar.

        Lanza:
            `NotFoundException` si no existe un grupo con `grupo_id`.
        """
        if not self._repo.patch_section_permissions(grupo_id, seccion_id, to_add, to_remove):
            raise NotFoundException(f"Grupo con id={grupo_id} no encontrado")
