"""
Router HTTP para el recurso Sección.

Capa arquitectónica: Presentación / Routing HTTP.

Responsabilidades:
    - Exponer los endpoints REST CRUD del recurso `/seccion`.
    - Validar que el usuario autenticado posee el permiso requerido para cada
      operación (`AUDIT_SERV` para lectura, `MODIFY_SERV` para escritura).
    - Delegar toda la lógica de negocio en `SeccionService`.

Qué NO debe contener este fichero:
    - Lógica de negocio ni validaciones de dominio.
    - Acceso directo a la base de datos.
    - Gestión de la asociación sección-permisos de grupo. Eso pertenece a
      `routers/grupo_permisos.py`.

Contrato HTTP de este router:

    ┌──────────────────────────┬──────────────────┬──────────────────────────────────┐
    │ Método + Ruta            │ Permiso          │ Respuesta exitosa                │
    ├──────────────────────────┼──────────────────┼──────────────────────────────────┤
    │ GET    /seccion          │ AUDIT_SERV       │ 200 PagedResponse[SeccionRead]   │
    │ GET    /seccion/{id}     │ AUDIT_SERV       │ 200 SeccionRead                  │
    │ POST   /seccion          │ MODIFY_SERV      │ 201 IdResponse                   │
    │ PATCH  /seccion/{id}     │ MODIFY_SERV      │ 204 No Content                   │
    │ DELETE /seccion/{id}     │ MODIFY_SERV      │ 204 No Content                   │
    └──────────────────────────┴──────────────────┴──────────────────────────────────┘

    El dominio de permisos es `SERV` (servidor): las secciones son la unidad
    organizativa que agrupa servidores, por lo que su gestión exige permisos del
    ámbito de servidores, no del de usuarios (`USER`) ni del sistema (`SYS`).

    Las operaciones de escritura (`PATCH`, `DELETE`) devuelven `204 No Content`
    sin body. El cliente debe realizar un `GET` posterior si necesita el estado
    actualizado de la entidad.

Relaciones con otros módulos:
    - `core/database.py`           → `get_session` proporciona la `Session` de BD.
    - `core/dependencies.py`       → `require_permission` y `RequestUser`.
    - `models/common.py`           → `PagedResponse[SeccionRead]` e `IdResponse`.
    - `models/seccion.py`          → `SeccionCreate`, `SeccionPatch`, `SeccionRead`.
    - `services/seccion_service.py`→ delega la lógica de negocio.
    - `main.py`                    → registra este router con `app.include_router`.

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

from fastapi import APIRouter, Depends, Query, status
from sqlmodel import Session

from core.database import get_session
from core.dependencies import RequestUser, require_permission
from models.common import IdResponse, PagedResponse
from models.seccion import SeccionCreate, SeccionPatch, SeccionRead
from services.seccion_service import SeccionService

router = APIRouter(prefix="/seccion", tags=["Secciones"])


@router.get("", response_model=PagedResponse[SeccionRead])
def find_all(
    page: int = Query(default=0, ge=0, description="Página (base 0)"),
    size: int = Query(default=20, ge=1, le=100, description="Elementos por página"),
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("AUDIT_SERV"))] = None,  # type: ignore[assignment]
):
    """
    Devuelve una página de secciones.

    Requiere el permiso `AUDIT_SERV`. Las secciones son la unidad organizativa
    de los servidores, por lo que solo los usuarios con permisos de auditoría
    del ámbito de servidores pueden consultarlas.

    Args:
        page:    Número de página, base 0. Por defecto 0.
        size:    Elementos por página. Mínimo 1, máximo 100. Por defecto 20.
        session: Sesión de BD inyectada por `get_session`.
        _user:   Dependencia de autorización. No se usa en el cuerpo.

    Retorna:
        `PagedResponse[SeccionRead]` con las secciones de la página solicitada
        y los metadatos de paginación.

    Errores HTTP:
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `AUDIT_SERV`.
    """
    service = SeccionService(session)
    items, total = service.find_all(page=page, size=size)
    return PagedResponse(data=items, page=page, size=size, total=total)


@router.get("/{seccion_id}", response_model=SeccionRead)
def find_by_id(
    seccion_id: int,
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("AUDIT_SERV"))] = None,  # type: ignore[assignment]
):
    """
    Devuelve una sección por su identificador único.

    Args:
        seccion_id: Clave primaria de la sección a recuperar.
        session:    Sesión de BD inyectada por `get_session`.
        _user:      Dependencia de autorización. No se usa en el cuerpo.

    Retorna:
        `SeccionRead` con `id`, `nombre` y `descripcion` de la sección.

    Errores HTTP:
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `AUDIT_SERV`.
        404 Not Found    — no existe una sección con `seccion_id`.
    """
    service = SeccionService(session)
    return service.find_by_id(seccion_id)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=IdResponse)
def create(
    data: SeccionCreate,
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("MODIFY_SERV"))] = None,  # type: ignore[assignment]
):
    """
    Crea una nueva sección y devuelve su ID asignado.

    Devuelve `IdResponse` (solo el `id` auto-incremental asignado por la BD)
    en lugar del `SeccionRead` completo. El cliente debe realizar un
    `GET /seccion/{id}` posterior si necesita el objeto completo.

    Args:
        data:    Body `SeccionCreate` con `nombre` (obligatorio) y `descripcion`
                 (opcional).
        session: Sesión de BD inyectada por `get_session`.
        _user:   Dependencia de autorización. No se usa en el cuerpo.

    Retorna:
        `IdResponse` con el `id` de la sección recién creada.
        Código HTTP 201 Created.

    Errores HTTP:
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `MODIFY_SERV`.
        422 Unprocessable — body inválido (validación Pydantic).
        409 Conflict      — si existe una restricción de unicidad en BD
                            (gestionado por el handler global de `IntegrityError`).
    """
    service = SeccionService(session)
    new_id = service.insert(data)
    return IdResponse(id=new_id)


@router.patch("/{seccion_id}", status_code=status.HTTP_204_NO_CONTENT)
def update(
    seccion_id: int,
    patch: SeccionPatch,
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("MODIFY_SERV"))] = None,  # type: ignore[assignment]
):
    """
    Actualiza los campos de una sección existente (PATCH semántico).

    Solo modifica los campos incluidos en el body con valor no-`None`. Los
    campos ausentes o con valor `null` no se modifican (limitación de
    `exclude_none=True` en el repositorio; ver `SeccionRepository.update`).

    Devuelve `204 No Content` sin body tras la actualización. El cliente debe
    realizar un `GET /seccion/{id}` si necesita confirmar el estado resultante.

    Args:
        seccion_id: ID de la sección a actualizar.
        patch:      Body `SeccionPatch` con los campos a modificar.
        session:    Sesión de BD inyectada por `get_session`.
        _user:      Dependencia de autorización. No se usa en el cuerpo.

    Retorna:
        `204 No Content`.

    Errores HTTP:
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `MODIFY_SERV`.
        404 Not Found    — no existe una sección con `seccion_id`.
        422 Unprocessable — body inválido (validación Pydantic).
    """
    service = SeccionService(session)
    service.update(seccion_id, patch)


@router.delete("/{seccion_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(
    seccion_id: int,
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("MODIFY_SERV"))] = None,  # type: ignore[assignment]
):
    """
    Elimina una sección por su identificador único.

    La eliminación es a nivel ORM y activa las cascadas definidas en la BD.
    Si la sección tiene permisos de grupo asociados (`grupo_seccion`), estos se
    eliminan en cascada. Si la sección tiene servidores asociados, el
    comportamiento depende de la restricción de FK definida en BD (CASCADE o
    RESTRICT). Si la sección no existe, devuelve `HTTP 404`.

    Args:
        seccion_id: ID de la sección a eliminar.
        session:    Sesión de BD inyectada por `get_session`.
        _user:      Dependencia de autorización. No se usa en el cuerpo.

    Retorna:
        `204 No Content`.

    Errores HTTP:
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `MODIFY_SERV`.
        404 Not Found    — no existe una sección con `seccion_id`.
        409 Conflict      — si la BD rechaza el borrado por una FK sin CASCADE
                            (gestionado por el handler global de `IntegrityError`).
    """
    service = SeccionService(session)
    service.delete(seccion_id)
