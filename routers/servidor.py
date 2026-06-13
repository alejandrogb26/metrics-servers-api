"""
Router HTTP para el recurso Servidor.

Capa arquitectónica: Presentación / Routing HTTP.

Responsabilidades:
    - Exponer los diez endpoints REST del recurso `/servidor`: CRUD, creación y
      borrado en bulk, gestión de servicios asociados, subida de imagen y
      consulta de métricas de MongoDB.
    - Aplicar un filtro de visibilidad por sección sobre todos los endpoints:
      los usuarios sin superadmin solo ven y modifican servidores en las
      secciones para las que tienen el permiso correspondiente.
    - Delegar toda la lógica de negocio en `ServidorService`.

Qué NO debe contener este fichero:
    - Lógica de negocio ni validaciones de dominio.
    - Acceso directo a MariaDB, MongoDB ni MinIO.
    - Construcción del filtro de visibilidad por sección. Eso pertenece a
      `core/dependencies.py` (`visible_section_ids`).

Filtro de visibilidad por sección:
    A diferencia de otros routers donde `_user` es un guarda sin usar,
    en este router el resultado de `require_permission` se asigna a `user`
    (sin prefijo `_`) porque se pasa a `visible_section_ids(user, permiso)`.
    Esta función devuelve:
      - `None`          → usuario superadmin, ve todos los servidores.
      - `set[int]`      → conjunto de IDs de sección visibles para el permiso
                          solicitado. Puede ser vacío (sin acceso a nada).
    El `section_ids` resultante se pasa al servicio para filtrar las queries.

Contrato HTTP de este router:

    ┌────────────────────────────────────────┬───────────────┬──────────────────────────────┐
    │ Método + Ruta                          │ Permiso       │ Respuesta exitosa            │
    ├────────────────────────────────────────┼───────────────┼──────────────────────────────┤
    │ GET    /servidor                       │ AUDIT_SERV    │ 200 PagedResponse[ServidorRead] │
    │ GET    /servidor/{id}                  │ AUDIT_SERV    │ 200 ServidorRead             │
    │ POST   /servidor                       │ MODIFY_SERV   │ 201 ServidorRead             │
    │ POST   /servidor/bulk                  │ MODIFY_SERV   │ 201/207 BulkResult           │
    │ PATCH  /servidor/{id}                  │ MODIFY_SERV   │ 204 No Content               │
    │ DELETE /servidor/{id}                  │ MODIFY_SERV   │ 204 No Content               │
    │ POST   /servidor/bulk-delete           │ MODIFY_SERV   │ 200 BulkResult               │
    │ POST   /servidor/{id}/servicios        │ MODIFY_SERV   │ 200 CountResult              │
    │ DELETE /servidor/{id}/servicios        │ MODIFY_SERV   │ 200 CountResult              │
    │ POST   /servidor/{id}/foto             │ MODIFY_SERV   │ 200 UploadResult             │
    │ GET    /servidor/{server_id}/metrics   │ AUDIT_SERV    │ 200 list[dict]               │
    └────────────────────────────────────────┴───────────────┴──────────────────────────────┘

    `create_bulk` devuelve 201 si todos los servidores se crearon correctamente,
    207 Multi-Status si hubo fallos parciales. El código se fija dinámicamente
    mediante `JSONResponse` porque FastAPI no admite códigos de respuesta
    condicionales en `status_code`.

    `get_metrics` usa `server_id: str` (ID externo del servidor) en lugar de
    `servidor_id: int` (PK de MariaDB) porque las métricas en MongoDB se
    indexan por el identificador externo del agente de monitorización.

Relaciones con otros módulos:
    - `core/database.py`            → `get_session` proporciona la `Session` de BD.
    - `core/dependencies.py`        → `require_permission`, `RequestUser` y
                                      `visible_section_ids` (filtro de sección).
    - `models/common.py`            → `BulkResult`, `CountResult`, `IdResponse`,
                                      `PagedResponse`, `UploadResult`.
    - `models/servidor.py`          → `ServidorCreate`, `ServidorPatchRequest`,
                                      `ServidorRead`.
    - `services/servidor_service.py`→ delega toda la lógica de negocio.
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
from core.dependencies import RequestUser, require_permission, visible_section_ids
from models.common import BulkResult, CountResult, IdResponse, PagedResponse, UploadResult
from models.servidor import ServidorCreate, ServidorPatchRequest, ServidorRead
from services.servidor_service import ServidorService

router = APIRouter(prefix="/servidor", tags=["Servidores"])


@router.get("", response_model=PagedResponse[ServidorRead])
def find_all(
    page: int = Query(default=0, ge=0, description="Página (base 0)"),
    size: int = Query(default=20, ge=1, le=100, description="Elementos por página"),
    session: Session = Depends(get_session),
    user: Annotated[RequestUser, Depends(require_permission("AUDIT_SERV"))] = None,  # type: ignore[assignment]
):
    """
    Devuelve una página de servidores visibles para el usuario autenticado.

    El conjunto de servidores devueltos está filtrado por las secciones que el
    usuario puede ver con el permiso `AUDIT_SERV`. Un superadmin recibe todos
    los servidores; un usuario normal solo ve los de sus secciones autorizadas.
    Si el usuario no tiene acceso a ninguna sección, la respuesta es una página
    vacía (no un error 403).

    Args:
        page:    Número de página, base 0. Por defecto 0.
        size:    Elementos por página. Mínimo 1, máximo 100. Por defecto 20.
        session: Sesión de BD inyectada por `get_session`.
        user:    Usuario autenticado. Se usa para calcular `section_ids`.

    Retorna:
        `PagedResponse[ServidorRead]` filtrado por secciones visibles.

    Errores HTTP:
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `AUDIT_SERV`.
    """
    svc = ServidorService(session)
    section_ids = visible_section_ids(user, "AUDIT_SERV")
    items, total = svc.find_all(page=page, size=size, section_ids=section_ids)
    return PagedResponse(data=items, page=page, size=size, total=total)


@router.get("/{servidor_id}", response_model=ServidorRead)
def find_by_id(
    servidor_id: int,
    session: Session = Depends(get_session),
    user: Annotated[RequestUser, Depends(require_permission("AUDIT_SERV"))] = None,  # type: ignore[assignment]
):
    """
    Devuelve un servidor por su identificador único si es visible para el usuario.

    Si el servidor existe pero pertenece a una sección fuera del alcance del
    usuario, el servicio devuelve `None` y el handler eleva `HTTP 404`. Esto
    evita revelar la existencia de servidores en secciones no autorizadas
    (comportamiento de seguridad por oscuridad).

    Args:
        servidor_id: Clave primaria del servidor (PK de MariaDB).
        session:     Sesión de BD inyectada por `get_session`.
        user:        Usuario autenticado. Se usa para calcular `section_ids`.

    Retorna:
        `ServidorRead` con todos los campos del servidor, incluyendo la lista
        de servicios asociados y las URLs de imagen.

    Errores HTTP:
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `AUDIT_SERV`.
        404 Not Found    — servidor inexistente o fuera del alcance del usuario.
    """
    svc = ServidorService(session)
    section_ids = visible_section_ids(user, "AUDIT_SERV")
    return svc.find_by_id(servidor_id, section_ids=section_ids)


@router.post("", response_model=ServidorRead, status_code=status.HTTP_201_CREATED)
def create(
    dto: ServidorCreate,
    session: Session = Depends(get_session),
    user: Annotated[RequestUser, Depends(require_permission("MODIFY_SERV"))] = None,  # type: ignore[assignment]
):
    """
    Crea un nuevo servidor y devuelve el objeto completo con el ID asignado.

    Antes de insertar, comprueba que la sección destino (`dto.seccion_id`) está
    dentro de las secciones visibles para el usuario. Si no lo está, devuelve
    `HTTP 404` en lugar de `403` para no revelar la existencia de la sección.

    A diferencia de `routers/seccion.py` y `routers/servicio.py` (que devuelven
    `IdResponse`), este endpoint devuelve el `ServidorRead` completo (incluyendo
    los datos obtenidos por el probe SSH en la inserción).

    Args:
        dto:     Body `ServidorCreate` con los datos del servidor y la lista
                 inicial de servicios a asociar.
        session: Sesión de BD inyectada por `get_session`.
        user:    Usuario autenticado. Se usa para validar la sección destino.

    Retorna:
        `ServidorRead` completo del servidor recién creado.
        Código HTTP 201 Created.

    Errores HTTP:
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `MODIFY_SERV`.
        404 Not Found    — sección no accesible para el usuario.
        422 Unprocessable — body inválido (validación Pydantic).
    """
    section_ids = visible_section_ids(user, "MODIFY_SERV")
    if section_ids is not None and dto.seccion_id not in section_ids:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Servidor no encontrado")
    svc = ServidorService(session)
    return svc.insert(dto)


@router.post("/bulk", response_model=BulkResult)
def create_bulk(
    servidores: list[ServidorCreate],
    session: Session = Depends(get_session),
    user: Annotated[RequestUser, Depends(require_permission("MODIFY_SERV"))] = None,  # type: ignore[assignment]
):
    """
    Crea múltiples servidores en una sola petición.

    Valida la accesibilidad de las secciones antes de insertar: si algún
    servidor apunta a una sección fuera del alcance del usuario, devuelve
    `HTTP 404` con la lista de `server_id` rechazados antes de insertar ninguno.

    Código de respuesta dinámico:
        - `201 Created`      — todos los servidores se crearon correctamente.
        - `207 Multi-Status` — al menos uno falló (p.ej. por error de BD o probe
          SSH). El body `BulkResult` detalla el número de éxitos y fallos.

    El código dinámico se implementa mediante `JSONResponse` con `status_code`
    calculado, ya que FastAPI no permite declarar códigos de respuesta
    condicionales en el decorador. El `response_model=BulkResult` en el decorador
    solo afecta a la documentación OpenAPI; en runtime, `JSONResponse` se devuelve
    directamente sin pasar por el validador de Pydantic.

    Args:
        servidores: Body: lista de `ServidorCreate`. Debe tener al menos un elemento.
        session:    Sesión de BD inyectada por `get_session`.
        user:       Usuario autenticado. Se usa para validar las secciones destino.

    Retorna:
        `BulkResult` con contadores `created` y `failed`.
        Código HTTP 201 si todos correctos, 207 si fallos parciales.

    Errores HTTP:
        400 Bad Request  — lista vacía.
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `MODIFY_SERV`.
        404 Not Found    — algún servidor apunta a sección fuera del alcance.
    """
    if not servidores:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Lista vacía")
    section_ids = visible_section_ids(user, "MODIFY_SERV")
    if section_ids is not None:
        unauthorized = [s.server_id for s in servidores if s.seccion_id not in section_ids]
        if unauthorized:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Sección no encontrada para: {', '.join(unauthorized)}",
            )
    svc = ServidorService(session)
    result = svc.insert_bulk(servidores)
    status_code = status.HTTP_201_CREATED if result.failed == 0 else 207
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=status_code, content=result.model_dump())


# ── Borrado ───────────────────────────────────────────────────────────────────
# CONTRATO: DELETE simple por ID; borrado múltiple via POST /bulk-delete.
# Se elimina el DELETE con body (incompatible con clientes HTTP estándar).

@router.patch("/{servidor_id}", status_code=status.HTTP_204_NO_CONTENT)
def patch_servidor(
    servidor_id: int,
    patch: ServidorPatchRequest,
    session: Session = Depends(get_session),
    user: Annotated[RequestUser, Depends(require_permission("MODIFY_SERV"))] = None,  # type: ignore[assignment]
):
    """
    Actualiza los campos editables de un servidor (PATCH semántico).

    `ServidorPatchRequest` expone solo los campos que el usuario puede modificar
    directamente: `dns`, `seccionId` y `serviciosIds`. Los campos obtenidos por
    probe SSH (`hostname`, `prettyOs`, `arch`, `kernel`) no forman parte del DTO
    de solicitud pública.

    El servicio aplica el filtro de sección: si el servidor no es visible para
    el usuario (no está en sus secciones autorizadas), devuelve `False` y el
    handler eleva `HTTP 404`.

    Args:
        servidor_id: PK del servidor a actualizar.
        patch:       Body `ServidorPatchRequest` con los campos a modificar.
        session:     Sesión de BD inyectada por `get_session`.
        user:        Usuario autenticado. Se usa para calcular `section_ids`.

    Retorna:
        `204 No Content`.

    Errores HTTP:
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `MODIFY_SERV`.
        404 Not Found    — servidor inexistente o fuera del alcance del usuario.
        422 Unprocessable — body inválido (validación Pydantic).
    """
    svc = ServidorService(session)
    section_ids = visible_section_ids(user, "MODIFY_SERV")
    svc.update(servidor_id, patch, section_ids=section_ids)


@router.delete("/{servidor_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_servidor(
    servidor_id: int,
    session: Session = Depends(get_session),
    user: Annotated[RequestUser, Depends(require_permission("MODIFY_SERV"))] = None,  # type: ignore[assignment]
):
    """
    Elimina un servidor por su identificador único.

    Aplica el filtro de sección: si el servidor no es visible para el usuario,
    devuelve `HTTP 404`. El servicio coordina la eliminación del registro en
    MariaDB y la limpieza de métricas en MongoDB.

    Args:
        servidor_id: PK del servidor a eliminar.
        session:     Sesión de BD inyectada por `get_session`.
        user:        Usuario autenticado. Se usa para calcular `section_ids`.

    Retorna:
        `204 No Content`.

    Errores HTTP:
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `MODIFY_SERV`.
        404 Not Found    — servidor inexistente o fuera del alcance del usuario.
    """
    svc = ServidorService(session)
    section_ids = visible_section_ids(user, "MODIFY_SERV")
    svc.delete(servidor_id, section_ids=section_ids)


@router.post("/bulk-delete", response_model=BulkResult)
def delete_bulk(
    ids: list[int],
    session: Session = Depends(get_session),
    user: Annotated[RequestUser, Depends(require_permission("MODIFY_SERV"))] = None,  # type: ignore[assignment]
):
    """
    Elimina múltiples servidores.
    Body: lista de IDs enteros → [1, 2, 3]

    Se implementa como `POST /bulk-delete` en lugar de `DELETE` con body porque
    algunos clientes HTTP (navegadores, proxies) no admiten cuerpo en peticiones
    DELETE. El servicio aplica el filtro de sección sobre el conjunto de IDs.

    Args:
        ids:     Body: lista de PKs de servidores a eliminar.
        session: Sesión de BD inyectada por `get_session`.
        user:    Usuario autenticado. Se usa para calcular `section_ids`.

    Retorna:
        `BulkResult` con `deleted` (eliminados) y `failed` (no encontrados o
        fuera del alcance del usuario).

    Errores HTTP:
        400 Bad Request  — lista vacía.
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `MODIFY_SERV`.
    """
    if not ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Lista vacía")
    svc = ServidorService(session)
    section_ids = visible_section_ids(user, "MODIFY_SERV")
    return svc.delete_bulk(ids, section_ids=section_ids)


# ── Servicios asociados ────────────────────────────────────────────────────────
# Añadir: POST /{servidor_id}/servicios   body: [1, 2, 3]
# Quitar: DELETE /{servidor_id}/servicios  query: ?ids=1&ids=2&ids=3

@router.post("/{servidor_id}/servicios", response_model=CountResult)
def add_servicios(
    servidor_id: int,
    servicio_ids: list[int],
    session: Session = Depends(get_session),
    user: Annotated[RequestUser, Depends(require_permission("MODIFY_SERV"))] = None,  # type: ignore[assignment]
):
    """
    Asocia servicios a un servidor. Body: lista de IDs de servicio.

    La asociación usa `INSERT IGNORE` en el repositorio: si el par
    (servidor, servicio) ya existe, se silencia sin error. El contador devuelto
    en `CountResult.count` refleja el número de IDs enviados en el body, no el
    número de filas realmente insertadas.

    Aplica el filtro de sección: si el servidor no es visible para el usuario,
    el servicio devuelve `None` y el handler eleva `HTTP 404`.

    Args:
        servidor_id:  PK del servidor al que se asocian los servicios.
        servicio_ids: Body: lista de IDs de servicio a asociar.
        session:      Sesión de BD inyectada por `get_session`.
        user:         Usuario autenticado. Se usa para calcular `section_ids`.

    Retorna:
        `CountResult` con el número de IDs enviados (no de filas insertadas).

    Errores HTTP:
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `MODIFY_SERV`.
        404 Not Found    — servidor inexistente o fuera del alcance del usuario.
    """
    svc = ServidorService(session)
    section_ids = visible_section_ids(user, "MODIFY_SERV")
    return CountResult(count=svc.add_servicios(servidor_id, servicio_ids, section_ids=section_ids))


@router.delete("/{servidor_id}/servicios", response_model=CountResult)
def remove_servicios(
    servidor_id: int,
    ids: list[int] = Query(description="IDs de servicios a desasociar"),
    session: Session = Depends(get_session),
    user: Annotated[RequestUser, Depends(require_permission("MODIFY_SERV"))] = None,  # type: ignore[assignment]
):
    """
    Desasocia servicios de un servidor.
    Query params: ?ids=1&ids=2&ids=3

    Los IDs de servicio se pasan como query parameters repetidos (`?ids=1&ids=2`)
    en lugar de en el body, porque algunos clientes HTTP no admiten cuerpo en
    peticiones DELETE. El contador devuelto refleja las filas realmente eliminadas
    de `servidores_servicios`.

    Aplica el filtro de sección: si el servidor no es visible para el usuario,
    el servicio devuelve `None` y el handler eleva `HTTP 404`.

    Args:
        servidor_id: PK del servidor del que se desasocian los servicios.
        ids:         Query params: lista de IDs de servicio a desasociar.
        session:     Sesión de BD inyectada por `get_session`.
        user:        Usuario autenticado. Se usa para calcular `section_ids`.

    Retorna:
        `CountResult` con el número de asociaciones efectivamente eliminadas.

    Errores HTTP:
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `MODIFY_SERV`.
        404 Not Found    — servidor inexistente o fuera del alcance del usuario.
    """
    svc = ServidorService(session)
    section_ids = visible_section_ids(user, "MODIFY_SERV")
    return CountResult(count=svc.remove_servicios(servidor_id, ids, section_ids=section_ids))


# ── Foto ───────────────────────────────────────────────────────────────────────

@router.post("/{servidor_id}/foto", response_model=UploadResult)
async def upload_foto(
    servidor_id: int,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    user: Annotated[RequestUser, Depends(require_permission("MODIFY_SERV"))] = None,  # type: ignore[assignment]
):
    """
    Sube o reemplaza la imagen de un servidor en MinIO.

    Flujo de la operación:
        1. Verifica que el servidor existe y es visible para el usuario (404 si no).
        2. Verifica que se ha enviado un fichero con nombre (400 si falta).
        3. Lee el contenido completo del fichero en memoria (`await file.read()`).
        4. Delega en `ServidorService.update_foto`, que sube el fichero a MinIO
           y persiste el nombre resultante en BD.
        5. Devuelve `UploadResult` con el nombre del fichero almacenado.

    Es `async def` porque requiere `await file.read()`.

    A diferencia de `routers/servicio.py` (`upload_logo`), este endpoint devuelve
    `UploadResult` solo con `nombre_archivo` (sin `url_foto`). El cliente debe
    derivar la URL a partir del nombre si la necesita.

    Args:
        servidor_id: PK del servidor cuya imagen se sube.
        file:        Fichero a subir (multipart/form-data). Campo requerido.
        session:     Sesión de BD inyectada por `get_session`.
        user:        Usuario autenticado. Se usa para calcular `section_ids`.

    Retorna:
        `UploadResult` con `nombre_archivo`. El campo `url_foto` es `None`.

    Errores HTTP:
        400 Bad Request  — fichero recibido sin nombre.
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `MODIFY_SERV`.
        404 Not Found    — servidor inexistente o fuera del alcance del usuario.
    """
    svc = ServidorService(session)
    section_ids = visible_section_ids(user, "MODIFY_SERV")
    svc.find_by_id(servidor_id, section_ids=section_ids)  # raises NotFoundException if not visible
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Archivo no proporcionado")

    data = await file.read()
    nombre = svc.update_foto(servidor_id, data, file.filename)
    return UploadResult(nombre_archivo=nombre)


# ── Métricas ───────────────────────────────────────────────────────────────────

@router.get("/{server_id}/metrics", response_model=list[dict])
def get_metrics(
    server_id: str,
    minutes: int = Query(default=60, ge=1, le=1440, description="Minutos hacia atrás (máx. 24 h)"),
    session: Session = Depends(get_session),
    user: Annotated[RequestUser, Depends(require_permission("AUDIT_SERV"))] = None,  # type: ignore[assignment]
):
    """
    Devuelve métricas del servidor en los últimos N minutos.
    Retorna lista vacía (200) si no hay datos en el intervalo.

    El parámetro de ruta es `server_id: str`, el identificador externo del
    agente de monitorización, distinto de `servidor_id: int` (PK de MariaDB)
    usado por los otros endpoints. Las métricas en MongoDB se indexan por este
    identificador externo.

    El servicio verifica que el `server_id` corresponde a un servidor visible
    para el usuario (usando `section_ids`) antes de consultar MongoDB. Si el
    servidor no existe en MariaDB o no es accesible, devuelve `None` y el
    handler eleva `HTTP 404`.

    La ventana de tiempo máxima es de 24 horas (`le=1440` minutos). El número
    máximo de documentos devueltos está limitado por `MongoRepository.MAX_DOCUMENTS`
    (10 000) independientemente de la ventana solicitada.

    Args:
        server_id: Identificador externo del servidor (string, no PK de MariaDB).
        minutes:   Ventana de tiempo hacia atrás en minutos. Rango [1, 1440].
                   Por defecto 60 (última hora).
        session:   Sesión de BD inyectada por `get_session`.
        user:      Usuario autenticado. Se usa para calcular `section_ids`.

    Retorna:
        Lista de documentos dict en orden cronológico ascendente.
        Lista vacía si no hay métricas en el intervalo.

    Errores HTTP:
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `AUDIT_SERV`.
        404 Not Found    — servidor inexistente o fuera del alcance del usuario.
    """
    svc = ServidorService(session)
    section_ids = visible_section_ids(user, "AUDIT_SERV")
    return svc.get_metrics(server_id, minutes, section_ids=section_ids)
