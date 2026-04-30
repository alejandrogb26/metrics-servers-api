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
    service = ServicioService(session)
    items, total = service.find_all(page=page, size=size)
    return PagedResponse(data=items, page=page, size=size, total=total)


@router.get("/{servicio_id}", response_model=ServicioRead)
def find_by_id(
    servicio_id: int,
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("AUDIT_SERV"))] = None,  # type: ignore[assignment]
):
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
    svc = ServicioService(session)
    return IdResponse(id=svc.insert(data))


@router.patch("/{servicio_id}", status_code=status.HTTP_204_NO_CONTENT)
def update(
    servicio_id: int,
    patch: ServicioPatch,
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("MODIFY_SERV"))] = None,  # type: ignore[assignment]
):
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
    svc = ServicioService(session)
    if not svc.delete(servicio_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Servicio no encontrado")
