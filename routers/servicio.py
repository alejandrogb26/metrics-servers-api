"""
Router HTTP para el recurso Servicio (CRUD + subida de logo).

Capa arquitectónica: Presentación / Routing HTTP.

Responsabilidades:
    - Exponer los endpoints REST CRUD del recurso `/servicio`.
    - Gestionar la subida del logo del servicio a través de un endpoint dedicado
      (`POST /{id}/logo`), separado del ciclo de vida CRUD del servicio.
    - Validar que el usuario autenticado posee el permiso requerido para cada
      operación (`AUDIT_SERV` para lectura, `MODIFY_SERV` para escritura).
    - Delegar toda la lógica de negocio en `ServicioService`.

Qué NO debe contener este fichero:
    - Lógica de negocio ni validaciones de dominio.
    - Acceso directo a la base de datos ni a MinIO.
    - Transformación de `logo` (nombre de fichero) a `url_logo`. Eso pertenece
      a `services/servicio_service.py`.

Contrato HTTP de este router:

    ┌──────────────────────────────────┬──────────────────┬──────────────────────────────────┐
    │ Método + Ruta                    │ Permiso          │ Respuesta exitosa                │
    ├──────────────────────────────────┼──────────────────┼──────────────────────────────────┤
    │ GET    /servicio                 │ AUDIT_SERV       │ 200 PagedResponse[ServicioRead]  │
    │ GET    /servicio/{id}            │ AUDIT_SERV       │ 200 ServicioRead                 │
    │ POST   /servicio                 │ MODIFY_SERV      │ 201 IdResponse                   │
    │ PATCH  /servicio/{id}            │ MODIFY_SERV      │ 204 No Content                   │
    │ POST   /servicio/{id}/logo       │ MODIFY_SERV      │ 200 UploadResult                 │
    │ DELETE /servicio/{id}            │ MODIFY_SERV      │ 204 No Content                   │
    └──────────────────────────────────┴──────────────────┴──────────────────────────────────┘

    `ServicioRead` expone `url_logo` (URL pública en MinIO) en lugar del campo
    interno `logo` (nombre del fichero). La generación de la URL la hace el
    servicio al construir el DTO.

    El logo tiene un ciclo de vida separado del servicio: se crea sin logo y se
    asigna posteriormente mediante `POST /{id}/logo`. El handler `upload_logo`
    es la única función `async def` del router porque requiere `await file.read()`.

Relaciones con otros módulos:
    - `core/database.py`            → `get_session` proporciona la `Session` de BD.
    - `core/dependencies.py`        → `require_permission` y `RequestUser`.
    - `models/common.py`            → `PagedResponse`, `IdResponse`, `UploadResult`.
    - `models/servicio.py`          → `ServicioCreate`, `ServicioPatch`, `ServicioRead`.
    - `services/servicio_service.py`→ delega CRUD y la subida del logo a MinIO.
    - `main.py`                     → registra este router con `app.include_router`.

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

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlmodel import Session

from core.database import get_session
from core.dependencies import RequestUser, require_permission
from models.common import IdResponse, PagedResponse, UploadResult
from models.servicio import ServicioCreate, ServicioPatch, ServicioRead
from services.servicio_service import ServicioService

router = APIRouter(prefix="/servicio", tags=["Servicios"])


@router.get("", response_model=PagedResponse[ServicioRead])
def find_all(
    page: int = Query(default=0, ge=0, description="Página (base 0)"),
    size: int = Query(default=20, ge=1, le=100, description="Elementos por página"),
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("AUDIT_SERV"))] = None,  # type: ignore[assignment]
):
    """
    Devuelve una página de servicios con sus URLs de logo generadas.

    Cada `ServicioRead` de la lista incluye `url_logo` (URL pública del logo
    en MinIO) en lugar del campo interno `logo`. Si el servicio no tiene logo
    asignado, `url_logo` es `None`.

    Args:
        page:    Número de página, base 0. Por defecto 0.
        size:    Elementos por página. Mínimo 1, máximo 100. Por defecto 20.
        session: Sesión de BD inyectada por `get_session`.
        _user:   Dependencia de autorización. No se usa en el cuerpo.

    Retorna:
        `PagedResponse[ServicioRead]` con los servicios de la página solicitada.

    Errores HTTP:
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `AUDIT_SERV`.
    """
    service = ServicioService(session)
    items, total = service.find_all(page=page, size=size)
    return PagedResponse(data=items, page=page, size=size, total=total)


@router.get("/{servicio_id}", response_model=ServicioRead)
def find_by_id(
    servicio_id: int,
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("AUDIT_SERV"))] = None,  # type: ignore[assignment]
):
    """
    Devuelve un servicio por su identificador único.

    Args:
        servicio_id: Clave primaria del servicio a recuperar.
        session:     Sesión de BD inyectada por `get_session`.
        _user:       Dependencia de autorización. No se usa en el cuerpo.

    Retorna:
        `ServicioRead` con `id`, `nombre` y `url_logo`.

    Errores HTTP:
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `AUDIT_SERV`.
        404 Not Found    — no existe un servicio con `servicio_id`.
    """
    svc = ServicioService(session)
    result = svc.find_by_id(servicio_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Servicio no encontrado")
    return result


@router.post("", status_code=status.HTTP_201_CREATED, response_model=IdResponse)
def create(
    data: ServicioCreate,
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("MODIFY_SERV"))] = None,  # type: ignore[assignment]
):
    """
    Crea un nuevo servicio y devuelve su ID asignado.

    El servicio se crea sin logo. El logo se asigna en una operación posterior
    mediante `POST /{servicio_id}/logo`. Esto es coherente con el modelo
    `Servicio` donde `logo` tiene un ciclo de vida independiente del resto de
    los campos.

    Devuelve solo `IdResponse` (el ID auto-incremental). El cliente debe hacer
    un `GET /servicio/{id}` posterior si necesita el objeto completo.

    Args:
        data:    Body `ServicioCreate` con el `nombre` del servicio.
        session: Sesión de BD inyectada por `get_session`.
        _user:   Dependencia de autorización. No se usa en el cuerpo.

    Retorna:
        `IdResponse` con el `id` del servicio recién creado.
        Código HTTP 201 Created.

    Errores HTTP:
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `MODIFY_SERV`.
        422 Unprocessable — body inválido (validación Pydantic).
        409 Conflict      — nombre de servicio duplicado (handler global de
                            `IntegrityError`).
    """
    svc = ServicioService(session)
    return IdResponse(id=svc.insert(data))


@router.patch("/{servicio_id}", status_code=status.HTTP_204_NO_CONTENT)
def update(
    servicio_id: int,
    patch: ServicioPatch,
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("MODIFY_SERV"))] = None,  # type: ignore[assignment]
):
    """
    Actualiza el nombre de un servicio existente (PATCH semántico).

    `ServicioPatch` solo permite modificar `nombre`. El campo `logo` no forma
    parte del DTO de patch y nunca se modifica por este endpoint; eso es
    responsabilidad exclusiva de `POST /{id}/logo`.

    Devuelve `204 No Content` sin body. El cliente debe realizar un
    `GET /servicio/{id}` si necesita confirmar el estado actualizado.

    Args:
        servicio_id: ID del servicio a actualizar.
        patch:       Body `ServicioPatch` con el nuevo `nombre`.
        session:     Sesión de BD inyectada por `get_session`.
        _user:       Dependencia de autorización. No se usa en el cuerpo.

    Retorna:
        `204 No Content`.

    Errores HTTP:
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `MODIFY_SERV`.
        404 Not Found    — no existe un servicio con `servicio_id`.
        422 Unprocessable — body inválido (validación Pydantic).
    """
    svc = ServicioService(session)
    if not svc.update(servicio_id, patch):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Servicio no encontrado")


@router.post("/{servicio_id}/logo", response_model=UploadResult)
async def upload_logo(
    servicio_id: int,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("MODIFY_SERV"))] = None,  # type: ignore[assignment]
):
    """
    Sube o reemplaza el logo de un servicio en MinIO y persiste el nombre del
    fichero en la base de datos.

    Endpoint dedicado para la gestión del logo, coherente con el ciclo de vida
    separado definido en `models/servicio.py`. Si el servicio ya tenía logo, se
    reemplaza (el fichero anterior en MinIO no se elimina automáticamente).

    Flujo de la operación:
        1. Verifica que el servicio existe (devuelve 404 si no).
        2. Verifica que se ha enviado un fichero con nombre (400 si falta).
        3. Lee el contenido completo del fichero en memoria (`await file.read()`).
        4. Delega en `ServicioService.update_logo`, que sube el fichero a MinIO
           y persiste el nombre resultante en BD.
        5. Devuelve `UploadResult` con el nombre del fichero y la URL pública.

    Es `async def` porque requiere `await file.read()` para leer el contenido
    del `UploadFile`. Todos los demás handlers de este router son síncronos.

    La validación de existencia en el paso 1 (`svc.find_by_id`) es una query
    previa separada de la operación de escritura, lo que introduce una
    condición de carrera TOCTOU: si el servicio se elimina entre la
    comprobación y la subida del fichero, la operación de MinIO podría
    completarse sin el registro correspondiente en BD.

    Args:
        servicio_id: ID del servicio cuyo logo se sube o reemplaza.
        file:        Fichero a subir (multipart/form-data). Campo requerido.
        session:     Sesión de BD inyectada por `get_session`.
        _user:       Dependencia de autorización. No se usa en el cuerpo.

    Retorna:
        `UploadResult` con `nombre_archivo` (nombre del fichero en MinIO) y
        `url_foto` (URL pública del logo).

    Errores HTTP:
        400 Bad Request  — fichero recibido sin nombre (`file.filename` vacío).
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `MODIFY_SERV`.
        404 Not Found    — no existe un servicio con `servicio_id`.
    """
    svc = ServicioService(session)
    if svc.find_by_id(servicio_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Servicio no encontrado")
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Archivo no proporcionado")
    data = await file.read()
    nombre, url = svc.update_logo(servicio_id, data, file.filename)
    return UploadResult(nombre_archivo=nombre, url_foto=url)


@router.delete("/{servicio_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(
    servicio_id: int,
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("MODIFY_SERV"))] = None,  # type: ignore[assignment]
):
    """
    Elimina un servicio por su identificador único.

    La eliminación es a nivel ORM. Si la BD tiene `ON DELETE CASCADE` sobre
    `servidores_servicios`, las asociaciones servidor-servicio existentes se
    eliminan en cascada. El fichero de logo en MinIO no se elimina
    automáticamente: debe gestionarse por separado si se requiere limpieza
    del almacenamiento de objetos.

    Args:
        servicio_id: ID del servicio a eliminar.
        session:     Sesión de BD inyectada por `get_session`.
        _user:       Dependencia de autorización. No se usa en el cuerpo.

    Retorna:
        `204 No Content`.

    Errores HTTP:
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `MODIFY_SERV`.
        404 Not Found    — no existe un servicio con `servicio_id`.
    """
    svc = ServicioService(session)
    if not svc.delete(servicio_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Servicio no encontrado")
