"""
Router HTTP para el recurso Permiso.

Capa arquitectónica: Presentación / Routing HTTP.

Responsabilidades:
    - Exponer los endpoints REST de solo lectura del recurso `/permisos`.
    - Validar que el usuario autenticado posee el permiso `AUDIT_USER`.
    - Delegar la lógica de consulta en `PermisoService`.

Qué NO debe contener este fichero:
    - Lógica de negocio ni validaciones de dominio.
    - Acceso directo a la base de datos.
    - Asignación de permisos a grupos. Eso pertenece a
      `routers/grupo_permisos.py`.

Contrato HTTP de este router:

    ┌────────────────────────────┬───────────────────┬──────────────────────────────────┐
    │ Método + Ruta              │ Permiso requerido │ Respuesta exitosa                │
    ├────────────────────────────┼───────────────────┼──────────────────────────────────┤
    │ GET /permisos              │ AUDIT_USER        │ 200 PagedResponse[PermisoRead]   │
    │ GET /permisos/{id}         │ AUDIT_USER        │ 200 PermisoRead                  │
    └────────────────────────────┴───────────────────┴──────────────────────────────────┘

    Los permisos son datos de catálogo de solo lectura. No existen endpoints de
    escritura porque los permisos se definen en el despliegue del sistema.

    `PermisoRead` incluye el `AmbitoRead` embebido (id, nombre, descripción del
    ámbito al que pertenece el permiso). La lista devuelta por `get_all` está
    ordenada por `(ambito.nombre, permiso.nombre)`, agrupando los permisos por
    ámbito de forma determinista.

    Se usa `AUDIT_USER` (no `AUDIT_SYS`) porque los permisos son entidades que
    definen capacidades de usuario, no parámetros de configuración del sistema.

Relaciones con otros módulos:
    - `core/database.py`          → `get_session` proporciona la `Session` de BD.
    - `core/dependencies.py`      → `require_permission` y `RequestUser`.
    - `models/common.py`          → `PagedResponse[PermisoRead]`.
    - `models/permiso.py`         → `PermisoRead` como esquema de respuesta.
    - `services/permiso_service.py` → delega la lógica de consulta y paginación.
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

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session

from core.database import get_session
from core.dependencies import RequestUser, require_permission
from models.common import PagedResponse
from models.permiso import PermisoRead
from services.permiso_service import PermisoService

router = APIRouter(prefix="/permisos", tags=["Permisos"])


@router.get("", response_model=PagedResponse[PermisoRead])
def get_all(
    page: int = Query(default=0, ge=0, description="Página (base 0)"),
    size: int = Query(default=50, ge=1, le=100, description="Elementos por página"),
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("AUDIT_USER"))] = None,  # type: ignore[assignment]
):
    """
    Devuelve una página de permisos, cada uno con su ámbito embebido.

    La lista está ordenada por `(ambito.nombre, permiso.nombre)` en la capa de
    repositorio, lo que agrupa los permisos por ámbito y garantiza un orden
    determinista independiente del ID.

    Requiere el permiso `AUDIT_USER`.

    Args:
        page:    Número de página, base 0. Por defecto 0.
        size:    Elementos por página. Mínimo 1, máximo 100. Por defecto 50.
        session: Sesión de BD inyectada por `get_session`.
        _user:   Dependencia de autorización. No se usa en el cuerpo.

    Retorna:
        `PagedResponse[PermisoRead]` con los permisos de la página solicitada.
        Cada `PermisoRead` incluye el `AmbitoRead` completo embebido.

    Errores HTTP:
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `AUDIT_USER`.
    """
    service = PermisoService(session)
    items, total = service.get_all(page=page, size=size)
    return PagedResponse(data=items, page=page, size=size, total=total)


@router.get("/{permiso_id}", response_model=PermisoRead)
def get_by_id(
    permiso_id: int,
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("AUDIT_USER"))] = None,  # type: ignore[assignment]
):
    """
    Devuelve un permiso por su identificador único, con el ámbito embebido.

    La consulta incluye un JOIN con la tabla `ambitos` para construir el
    `PermisoRead` completo en una sola query, sin carga diferida.

    Args:
        permiso_id: Clave primaria del permiso a recuperar.
        session:    Sesión de BD inyectada por `get_session`.
        _user:      Dependencia de autorización. No se usa en el cuerpo.

    Retorna:
        `PermisoRead` con nombre, descripción y `AmbitoRead` embebido.

    Errores HTTP:
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `AUDIT_USER`.
        404 Not Found    — no existe un permiso con `permiso_id`.
    """
    service = PermisoService(session)
    permiso = service.get_by_id(permiso_id)
    if permiso is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Permiso no encontrado")
    return permiso
