"""
Router HTTP para la gestión de permisos de un grupo.

Capa arquitectónica: Presentación / Routing HTTP (sub-recurso de grupos).

Responsabilidades:
    - Exponer los cuatro endpoints REST para leer y modificar los permisos
      asociados a un grupo, tanto globales como por sección.
    - Validar que el usuario autenticado posee el permiso `MODIFY_USER`.
    - Delegar las operaciones de modificación en `GrupoPermisosService` y
      la recarga del estado final en `GrupoService`.

Qué NO debe contener este fichero:
    - Lógica de asignación de permisos ni validación de IDs de permiso. Eso
      pertenece a `services/grupo_service.py` y `repositories/grupo_repo.py`.
    - Acceso directo a la base de datos.
    - Definición de los modelos de permiso (ámbito, nombre). Eso pertenece a
      `models/permiso.py` y `models/permission_map.py`.

Contrato HTTP de este router (prefijo: `/grupos/{grupo_id}/permisos`):

    ┌──────────────────────────────────────────────┬───────────────┬──────────────────────────┐
    │ Método + Ruta                                │ Body          │ Respuesta exitosa        │
    ├──────────────────────────────────────────────┼───────────────┼──────────────────────────┤
    │ PUT  /grupos/{id}/permisos                   │ PermissionMap │ 200 GrupoRead            │
    │ PATCH /grupos/{id}/permisos/global           │ PermisoPatch  │ 200 GrupoRead            │
    │ PUT  /grupos/{id}/permisos/secciones/{sid}   │ list[int]     │ 200 GrupoRead            │
    │ PATCH /grupos/{id}/permisos/secciones/{sid}  │ PermisoPatch  │ 200 GrupoRead            │
    └──────────────────────────────────────────────┴───────────────┴──────────────────────────┘

    Todos los endpoints requieren el permiso `MODIFY_USER`.
    Todos devuelven el `GrupoRead` completo (con permisos actualizados) tras la
    operación, recargado mediante una segunda llamada a `GrupoService.get_by_id`.

Patrón común de todos los endpoints:
    1. Llamar a `GrupoPermisosService` para modificar los permisos.
    2. Si el servicio devuelve `False` → el grupo no existe → `HTTP 404`.
    3. Llamar a `GrupoService(session).get_by_id(grupo_id)` para devolver el
       estado completo del grupo actualizado.

Relaciones con otros módulos:
    - `core/database.py`          → `get_session` proporciona la `Session` de BD.
    - `core/dependencies.py`      → `require_permission` y `RequestUser` para autorización.
    - `models/grupo.py`           → `GrupoRead` como esquema de respuesta.
    - `models/permission_map.py`  → `PermissionMap[int]` para el body de `replace_all`.
    - `services/grupo_service.py` → `GrupoPermisosService` (modificación) y
                                    `GrupoService` (recarga del estado final).
    - `main.py`                   → registra este router con `app.include_router`.

Autor:
    Alejandro Gómez Blanco

Proyecto:
    Metrics Servers

Versión:
    1.0.0

Organización:
    Metrics Servers Project
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session

from core.database import get_session
from core.dependencies import RequestUser, require_permission
from models.grupo import GrupoRead
from models.permission_map import PermissionMap
from services.grupo_service import GrupoPermisosService, GrupoService


class PermisoPatch(BaseModel):
    """
    Body para operaciones de modificación incremental de permisos (PATCH).

    Permite añadir y/o eliminar IDs de permiso en una sola petición.
    Ambos campos son opcionales: si se omiten o se envían como `null`, la
    operación correspondiente (add o remove) no se ejecuta. Si ambos son
    `null` o están ausentes, la llamada al servicio es un no-op.

    Campos:
        add:    Lista de IDs de permiso a añadir. `None` si no se quieren añadir.
        remove: Lista de IDs de permiso a eliminar. `None` si no se quieren eliminar.

    Se define localmente en este módulo porque solo se usa en estos endpoints y
    no forma parte del modelo de dominio compartido (`models/`).
    """

    add: list[int] | None = None
    remove: list[int] | None = None


# Router registrado en main.py. El prefijo incluye el path parameter {grupo_id},
# compartido por todos los endpoints de este router.
router = APIRouter(prefix="/grupos/{grupo_id}/permisos", tags=["Grupos – Permisos"])


@router.put("", response_model=GrupoRead)
def replace_all(
    grupo_id: int,
    permisos: PermissionMap[int],
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("MODIFY_USER"))] = None,  # type: ignore[assignment]
):
    """
    Reemplaza todos los permisos del grupo: globales y de todas las secciones.

    Operación destructiva: elimina el conjunto completo de permisos actuales
    del grupo (globales + todas las secciones) y los sustituye por los
    proporcionados en el body. Es el equivalente a un PUT sobre el sub-recurso
    completo de permisos.

    El body es un `PermissionMap[int]` con dos campos opcionales:
      - `globalPerms`: lista de IDs de permisos globales (o `null` para dejar vacío).
      - `sections`:    mapa `{seccion_id: [permiso_id, ...]}` por sección (o `null`).

    Tras la modificación, recarga el grupo completo con `GrupoService.get_by_id`
    en una segunda operación sobre la misma sesión para devolver el estado real
    actualizado.

    Args:
        grupo_id: ID del grupo cuyos permisos se reemplazan.
        permisos: Body con los nuevos permisos globales y por sección.
        session:  Sesión de BD inyectada por `get_session`.
        _user:    Dependencia de autorización (requiere `MODIFY_USER`).

    Retorna:
        `GrupoRead` completo con los permisos actualizados.

    Errores HTTP:
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `MODIFY_USER`.
        404 Not Found    — no existe un grupo con `grupo_id`.
    """
    svc = GrupoPermisosService(session)
    svc.replace_all(grupo_id, permisos)
    return GrupoService(session).get_by_id(grupo_id)


@router.patch("/global", response_model=GrupoRead)
def patch_global(
    grupo_id: int,
    req: PermisoPatch,
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("MODIFY_USER"))] = None,  # type: ignore[assignment]
):
    """
    Modifica los permisos globales del grupo de forma incremental.

    A diferencia de `replace_all` (que borra y recrea todo), esta operación
    es incremental: añade los IDs de `req.add` y elimina los de `req.remove`
    sobre el conjunto de permisos globales existente. Los permisos de sección
    no se ven afectados.

    El body `PermisoPatch` admite:
      - Solo `add`: agrega permisos sin tocar los actuales.
      - Solo `remove`: elimina permisos sin tocar los actuales.
      - Ambos: add y remove se aplican en la misma operación.
      - Ninguno (`{}`): no-op; el servicio no modifica nada pero el grupo
        existe, por lo que se devuelve `200` con el estado actual.

    Args:
        grupo_id: ID del grupo cuyos permisos globales se modifican.
        req:      Body `PermisoPatch` con las listas `add` y/o `remove`.
        session:  Sesión de BD inyectada por `get_session`.
        _user:    Dependencia de autorización (requiere `MODIFY_USER`).

    Retorna:
        `GrupoRead` completo con los permisos globales actualizados.

    Errores HTTP:
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `MODIFY_USER`.
        404 Not Found    — no existe un grupo con `grupo_id`.
    """
    svc = GrupoPermisosService(session)
    svc.patch_global(grupo_id, req.add, req.remove)
    return GrupoService(session).get_by_id(grupo_id)


@router.put("/secciones/{seccion_id}", response_model=GrupoRead)
def replace_seccion(
    grupo_id: int,
    seccion_id: int,
    permiso_ids: list[int],
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("MODIFY_USER"))] = None,  # type: ignore[assignment]
):
    """
    Reemplaza todos los permisos de una sección concreta para el grupo.

    Operación acotada: solo afecta a los permisos de la sección `seccion_id`
    para el grupo `grupo_id`. Los permisos globales y los de otras secciones
    no se modifican.

    El body es directamente una lista de IDs de permiso (`list[int]`), a
    diferencia de `patch_seccion` que usa `PermisoPatch`. Una lista vacía
    `[]` borra todos los permisos de esa sección para el grupo.

    Args:
        grupo_id:    ID del grupo.
        seccion_id:  ID de la sección cuyos permisos se reemplazan.
        permiso_ids: Body: lista de IDs de permiso que deben quedar asignados.
                     Una lista vacía `[]` deja la sección sin permisos.
        session:     Sesión de BD inyectada por `get_session`.
        _user:       Dependencia de autorización (requiere `MODIFY_USER`).

    Retorna:
        `GrupoRead` completo con los permisos de la sección actualizados.

    Errores HTTP:
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `MODIFY_USER`.
        404 Not Found    — no existe un grupo con `grupo_id`.
    """
    svc = GrupoPermisosService(session)
    svc.replace_seccion(grupo_id, seccion_id, permiso_ids)
    return GrupoService(session).get_by_id(grupo_id)


@router.patch("/secciones/{seccion_id}", response_model=GrupoRead)
def patch_seccion(
    grupo_id: int,
    seccion_id: int,
    req: PermisoPatch,
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("MODIFY_USER"))] = None,  # type: ignore[assignment]
):
    """
    Modifica los permisos de una sección concreta para el grupo de forma incremental.

    Operación incremental acotada a una sola sección: añade los IDs de `req.add`
    y elimina los de `req.remove` sobre los permisos de la sección `seccion_id`
    para el grupo `grupo_id`. Los permisos globales y los de otras secciones
    no se ven afectados.

    El body `PermisoPatch` admite las mismas combinaciones que `patch_global`:
    solo `add`, solo `remove`, ambos, o ninguno (no-op).

    Args:
        grupo_id:   ID del grupo.
        seccion_id: ID de la sección cuyos permisos se modifican incrementalmente.
        req:        Body `PermisoPatch` con las listas `add` y/o `remove`.
        session:    Sesión de BD inyectada por `get_session`.
        _user:      Dependencia de autorización (requiere `MODIFY_USER`).

    Retorna:
        `GrupoRead` completo con los permisos de la sección actualizados.

    Errores HTTP:
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `MODIFY_USER`.
        404 Not Found    — no existe un grupo con `grupo_id`.
    """
    svc = GrupoPermisosService(session)
    svc.patch_seccion(grupo_id, seccion_id, req.add, req.remove)
    return GrupoService(session).get_by_id(grupo_id)
