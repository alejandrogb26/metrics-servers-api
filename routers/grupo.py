"""
Router HTTP para el recurso Grupo (CRUD + gestión del flag superAdmin).

Capa arquitectónica: Presentación / Routing HTTP.

Responsabilidades:
    - Exponer los endpoints REST del recurso `/grupos`.
    - Implementar dos niveles de autorización: el estándar basado en permiso
      (`require_permission`) y el de superadmin (comprobación manual de
      `RequestUser.superadmin` en el handler `set_superadmin`).
    - Delegar toda la lógica de negocio en `GrupoService`.

Qué NO debe contener este fichero:
    - Lógica de negocio ni validaciones de dominio.
    - Acceso directo a la base de datos.
    - Gestión de permisos del grupo (add/remove/replace). Eso pertenece a
      `routers/grupo_permisos.py`.

Contrato HTTP de este router:

    ┌─────────────────────────────────┬──────────────────────┬──────────────────────────────────┐
    │ Método + Ruta                   │ Permiso requerido    │ Respuesta exitosa                │
    ├─────────────────────────────────┼──────────────────────┼──────────────────────────────────┤
    │ GET    /grupos                  │ AUDIT_USER           │ 200 PagedResponse[GrupoRead]     │
    │ GET    /grupos/{id}             │ AUDIT_USER           │ 200 GrupoRead                    │
    │ POST   /grupos                  │ MODIFY_USER          │ 201 BulkResult                   │
    │ PATCH  /grupos/{id}             │ MODIFY_USER          │ 200 GrupoRead                    │
    │ PATCH  /grupos/{id}/superadmin  │ Solo autenticado *   │ 200 GrupoRead                    │
    │ DELETE /grupos/{id}             │ MODIFY_USER          │ 204 No Content                   │
    └─────────────────────────────────┴──────────────────────┴──────────────────────────────────┘

    * `set_superadmin` requiere autenticación válida (cualquier permiso) pero
      añade una segunda capa: el solicitante debe ser superadmin
      (`RequestUser.superadmin == True`). Esta verificación se realiza dentro
      del handler, no en la dependencia.

Relaciones con otros módulos:
    - `core/database.py`          → `get_session` proporciona la `Session` de BD.
    - `core/dependencies.py`      → `require_permission` y `RequestUser`.
    - `models/common.py`          → `PagedResponse[GrupoRead]` y `BulkResult`.
    - `models/grupo.py`           → `GrupoCreate`, `GrupoPatch`, `GrupoRead`,
                                    `SuperAdminPatch` como esquemas de entrada/salida.
    - `services/grupo_service.py` → `GrupoService` para toda la lógica de negocio.
    - `routers/grupo_permisos.py` → router hermano para la gestión de permisos.
    - `main.py`                   → registra ambos routers con `app.include_router`.

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

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlmodel import Session

from core.database import get_session
from core.dependencies import RequestUser, require_permission
from models.common import BulkResult, PagedResponse
from models.grupo import GrupoCreate, GrupoPatch, GrupoRead, SuperAdminPatch
from services.grupo_service import GrupoService

router = APIRouter(prefix="/grupos", tags=["Grupos"])


@router.get("", response_model=PagedResponse[GrupoRead])
def get_all(
    page: int = Query(default=0, ge=0, description="Página (base 0)"),
    size: int = Query(default=20, ge=1, le=100, description="Elementos por página"),
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("AUDIT_USER"))] = None,  # type: ignore[assignment]
):
    """
    Devuelve una página de grupos con sus permisos globales y por sección.

    Requiere el permiso `AUDIT_USER`. Los grupos son entidades de usuario
    (no de configuración de sistema), por lo que usan `AUDIT_USER` en lugar
    de `AUDIT_SYS`.

    El `GrupoRead` resultante incluye el campo `superadmin` (puede ser `None`
    para registros legados), los permisos globales y la mapa de permisos por
    sección, todos cargados por `GrupoService`.

    Args:
        page:    Número de página, base 0. Por defecto 0.
        size:    Elementos por página. Mínimo 1, máximo 100. Por defecto 20.
        session: Sesión de BD inyectada por `get_session`.
        _user:   Dependencia de autorización. No se usa en el cuerpo.

    Retorna:
        `PagedResponse[GrupoRead]` con la página de grupos y metadatos de
        paginación.

    Errores HTTP:
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `AUDIT_USER`.
    """
    svc = GrupoService(session)
    items, total = svc.get_all(page=page, size=size)
    return PagedResponse(data=items, page=page, size=size, total=total)


@router.get("/{grupo_id}", response_model=GrupoRead)
def get_by_id(
    grupo_id: int,
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("AUDIT_USER"))] = None,  # type: ignore[assignment]
):
    """
    Devuelve un grupo por su identificador único, con permisos completos.

    Args:
        grupo_id: Clave primaria del grupo a recuperar.
        session:  Sesión de BD inyectada por `get_session`.
        _user:    Dependencia de autorización. No se usa en el cuerpo.

    Retorna:
        `GrupoRead` con nombre, DN LDAP, flag superadmin y mapa de permisos.

    Errores HTTP:
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `AUDIT_USER`.
        404 Not Found    — no existe un grupo con `grupo_id`.
    """
    svc = GrupoService(session)
    grupo = svc.get_by_id(grupo_id)
    if grupo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grupo no encontrado")
    return grupo


@router.post("", status_code=status.HTTP_201_CREATED, response_model=BulkResult)
def create(
    grupos: list[GrupoCreate],
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("MODIFY_USER"))] = None,  # type: ignore[assignment]
):
    """
    Crea uno o varios grupos de forma atómica (bulk creation).

    El body es una lista de `GrupoCreate`, permitiendo crear múltiples grupos en
    una sola petición. Cada `GrupoCreate` incluye el nombre, el DN LDAP y
    opcionalmente los permisos iniciales (globales y por sección) que se asignan
    de forma atómica junto con la creación del grupo.

    A diferencia de los otros endpoints de escritura, no devuelve las entidades
    creadas sino un `BulkResult` con los contadores de éxito y fallo. Esto es
    coherente con la naturaleza batch de la operación: el cliente puede enviar
    N grupos y recibir cuántos se crearon correctamente.

    Args:
        grupos:  Body: lista de `GrupoCreate` con los datos de cada grupo.
                 Una lista de un solo elemento crea un único grupo.
        session: Sesión de BD inyectada por `get_session`.
        _user:   Dependencia de autorización. No se usa en el cuerpo.

    Retorna:
        `BulkResult` con `created` (éxitos) y `failed` (fallos).
        Código HTTP 201 Created.

    Errores HTTP:
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `MODIFY_USER`.
        422 Unprocessable — body inválido (validación Pydantic).
    """
    svc = GrupoService(session)
    return svc.create_bulk(grupos)


@router.patch("/{grupo_id}", response_model=GrupoRead)
def update(
    grupo_id: int,
    patch: GrupoPatch,
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("MODIFY_USER"))] = None,  # type: ignore[assignment]
):
    """
    Actualiza los campos editables de un grupo (PATCH semántico).

    `GrupoPatch` permite modificar `nombre` y/o `dn`. El campo `superadmin` está
    deliberadamente excluido de `GrupoPatch` por seguridad: solo puede cambiarse
    mediante el endpoint dedicado `PATCH /{grupo_id}/superadmin`.

    El servicio puede lanzar `ValueError` si la modificación viola una restricción
    semántica (p.ej. el DN ya pertenece a otro grupo). En ese caso el handler lo
    captura y devuelve `HTTP 422` con el mensaje de error.

    Tras la actualización se llama a `svc.get_by_id(grupo_id)` para devolver el
    estado real del grupo (con permisos completos), no solo el objeto parcheado.

    Args:
        grupo_id: ID del grupo a actualizar.
        patch:    Body `GrupoPatch` con los campos a modificar.
        session:  Sesión de BD inyectada por `get_session`.
        _user:    Dependencia de autorización. No se usa en el cuerpo.

    Retorna:
        `GrupoRead` completo con el estado actualizado del grupo.

    Errores HTTP:
        401 Unauthorized      — token ausente o inválido.
        403 Forbidden         — usuario sin permiso `MODIFY_USER`.
        404 Not Found         — no existe un grupo con `grupo_id`.
        422 Unprocessable     — la modificación viola una restricción semántica
                                (ValueError del servicio), o body inválido (Pydantic).
    """
    svc = GrupoService(session)
    try:
        found = svc.patch(grupo_id, patch)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    if not found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grupo no encontrado")
    return svc.get_by_id(grupo_id)


@router.patch("/{grupo_id}/superadmin", response_model=GrupoRead)
def set_superadmin(
    grupo_id: int,
    body: SuperAdminPatch,
    request: Request,
    session: Session = Depends(get_session),
    # Solo requiere autenticación; la verificación de superadmin se hace dentro
    _user: Annotated[RequestUser, Depends(require_permission())] = None,  # type: ignore[assignment]
):
    """
    Cambia el estado superAdmin de un grupo.
    Solo puede ser ejecutado por un superadmin (punto 3 de Java).

    Este endpoint implementa una autorización de dos niveles:
      1. `require_permission()` sin argumento: exige únicamente un token válido
         (el usuario está autenticado). No comprueba ningún permiso específico.
      2. Comprobación manual de `current_user.superadmin`: solo un usuario con
         el flag `superadmin` activo puede cambiar el flag de otro grupo. Esta
         segunda capa no puede expresarse como un nombre de permiso estándar
         porque depende del atributo dinámico del usuario, no de una entrada
         fija en la tabla de permisos.

    A diferencia de los demás endpoints donde `_user` es un guarda sin usar,
    aquí se reasigna a `current_user` para acceder a `RequestUser.superadmin`.
    El `# type: ignore[assignment]` es necesario porque Annotated+Depends
    produce `None` para el verificador de tipos.

    Args:
        grupo_id: ID del grupo cuyo flag superadmin se modifica.
        body:     Body `SuperAdminPatch` con el nuevo valor booleano de `superadmin`.
        request:  Objeto `Request` de FastAPI inyectado (disponible para
                  middleware o logging, no se usa directamente en el handler).
        session:  Sesión de BD inyectada por `get_session`.
        _user:    Dependencia de autenticación; se reasigna a `current_user`
                  para comprobar el flag superadmin del solicitante.

    Retorna:
        `GrupoRead` completo con el flag `superadmin` actualizado.

    Errores HTTP:
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — el solicitante no es superadmin.
        404 Not Found    — no existe un grupo con `grupo_id`.
    """
    # Verificar que el solicitante sea superadmin
    current_user: RequestUser = _user  # type: ignore[assignment]
    if not current_user.superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo un superadmin puede modificar el estado superAdmin de un grupo",
        )

    svc = GrupoService(session)
    if not svc.patch_superadmin(grupo_id, body.superadmin):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grupo no encontrado")
    return svc.get_by_id(grupo_id)


@router.delete("/{grupo_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(
    grupo_id: int,
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("MODIFY_USER"))] = None,  # type: ignore[assignment]
):
    """
    Elimina un grupo por su identificador único.

    La eliminación es a nivel ORM y activa las cascadas definidas en la BD.
    Si el grupo tiene permisos asociados (`grupo_permiso_global`, `grupo_seccion`),
    estos se eliminan en cascada. Si el grupo no existe, devuelve `HTTP 404`.

    Args:
        grupo_id: ID del grupo a eliminar.
        session:  Sesión de BD inyectada por `get_session`.
        _user:    Dependencia de autorización. No se usa en el cuerpo.

    Retorna:
        `204 No Content` si el grupo se eliminó correctamente.

    Errores HTTP:
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `MODIFY_USER`.
        404 Not Found    — no existe un grupo con `grupo_id`.
    """
    svc = GrupoService(session)
    if not svc.delete(grupo_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grupo no encontrado")
