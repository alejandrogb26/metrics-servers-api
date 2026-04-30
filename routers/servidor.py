from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlmodel import Session

from core.database import get_session
from core.dependencies import RequestUser, require_permission
from models.common import BulkResult, CountResult, IdResponse, PagedResponse, UploadResult
from models.servidor import ServidorCreate, ServidorPatchRequest, ServidorRead
from services.servidor_service import ServidorService

router = APIRouter(prefix="/servidor", tags=["Servidores"])


@router.get("", response_model=PagedResponse[ServidorRead])
def find_all(
    page: int = Query(default=0, ge=0, description="Página (base 0)"),
    size: int = Query(default=20, ge=1, le=100, description="Elementos por página"),
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("AUDIT_SERV"))] = None,  # type: ignore[assignment]
):
    svc = ServidorService(session)
    items, total = svc.find_all(page=page, size=size)
    return PagedResponse(data=items, page=page, size=size, total=total)


@router.get("/{servidor_id}", response_model=ServidorRead)
def find_by_id(
    servidor_id: int,
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("AUDIT_SERV"))] = None,  # type: ignore[assignment]
):
    svc = ServidorService(session)
    result = svc.find_by_id(servidor_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Servidor no encontrado")
    return result


@router.post("", response_model=ServidorRead, status_code=status.HTTP_201_CREATED)
def create(
    dto: ServidorCreate,
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("MODIFY_SERV"))] = None,  # type: ignore[assignment]
):
    svc = ServidorService(session)
    return svc.insert(dto)


@router.post("/bulk", response_model=BulkResult)
def create_bulk(
    servidores: list[ServidorCreate],
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("MODIFY_SERV"))] = None,  # type: ignore[assignment]
):
    if not servidores:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Lista vacía")
    svc = ServidorService(session)
    result = svc.insert_bulk(servidores)
    status_code = status.HTTP_201_CREATED if result.failed == 0 else 207
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=status_code, content=result.model_dump())


@router.patch("/{servidor_id}", status_code=status.HTTP_204_NO_CONTENT)
def patch_servidor(
    servidor_id: int,
    patch: ServidorPatchRequest,
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("MODIFY_SERV"))] = None,  # type: ignore[assignment]
):
    svc = ServidorService(session)
    if not svc.update(servidor_id, patch):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Servidor no encontrado")


# ── Borrado ───────────────────────────────────────────────────────────────────
# CONTRATO: DELETE simple por ID; borrado múltiple via POST /bulk-delete.
# Se elimina el DELETE con body (incompatible con clientes HTTP estándar).

@router.delete("/{servidor_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_servidor(
    servidor_id: int,
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("MODIFY_SERV"))] = None,  # type: ignore[assignment]
):
    svc = ServidorService(session)
    if not svc.delete(servidor_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Servidor no encontrado")


@router.post("/bulk-delete", response_model=BulkResult)
def delete_bulk(
    ids: list[int],
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("MODIFY_SERV"))] = None,  # type: ignore[assignment]
):
    """
    Elimina múltiples servidores.
    Body: lista de IDs enteros → [1, 2, 3]
    """
    if not ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Lista vacía")
    svc = ServidorService(session)
    return svc.delete_bulk(ids)


# ── Servicios asociados ────────────────────────────────────────────────────────
# Añadir: POST /{servidor_id}/servicios   body: [1, 2, 3]
# Quitar: DELETE /{servidor_id}/servicios  query: ?ids=1&ids=2&ids=3

@router.post("/{servidor_id}/servicios", response_model=CountResult)
def add_servicios(
    servidor_id: int,
    servicio_ids: list[int],
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("MODIFY_SERV"))] = None,  # type: ignore[assignment]
):
    """Asocia servicios a un servidor. Body: lista de IDs de servicio."""
    svc = ServidorService(session)
    added = svc.add_servicios(servidor_id, servicio_ids)
    if added is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Servidor no encontrado")
    return CountResult(count=added)


@router.delete("/{servidor_id}/servicios", response_model=CountResult)
def remove_servicios(
    servidor_id: int,
    ids: list[int] = Query(description="IDs de servicios a desasociar"),
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("MODIFY_SERV"))] = None,  # type: ignore[assignment]
):
    """
    Desasocia servicios de un servidor.
    Query params: ?ids=1&ids=2&ids=3
    """
    svc = ServidorService(session)
    removed = svc.remove_servicios(servidor_id, ids)
    if removed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Servidor no encontrado")
    return CountResult(count=removed)


# ── Foto ───────────────────────────────────────────────────────────────────────

@router.post("/{servidor_id}/foto", response_model=UploadResult)
async def upload_foto(
    servidor_id: int,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("MODIFY_SERV"))] = None,  # type: ignore[assignment]
):
    svc = ServidorService(session)
    servidor = svc.find_by_id(servidor_id)
    if servidor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Servidor no encontrado")
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
    _user: Annotated[RequestUser, Depends(require_permission("AUDIT_SERV"))] = None,  # type: ignore[assignment]
):
    """
    Devuelve métricas del servidor en los últimos N minutos.
    Retorna lista vacía (200) si no hay datos en el intervalo.
    """
    svc = ServidorService(session)
    result = svc.get_metrics(server_id, minutes)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Servidor no encontrado")
    return result
