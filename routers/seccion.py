from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
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
    service = SeccionService(session)
    items, total = service.find_all(page=page, size=size)
    return PagedResponse(data=items, page=page, size=size, total=total)


@router.get("/{seccion_id}", response_model=SeccionRead)
def find_by_id(
    seccion_id: int,
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("AUDIT_SERV"))] = None,  # type: ignore[assignment]
):
    service = SeccionService(session)
    seccion = service.find_by_id(seccion_id)
    if seccion is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sección no encontrada")
    return seccion


@router.post("", status_code=status.HTTP_201_CREATED, response_model=IdResponse)
def create(
    data: SeccionCreate,
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("MODIFY_SERV"))] = None,  # type: ignore[assignment]
):
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
    service = SeccionService(session)
    if not service.update(seccion_id, patch):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sección no encontrada")


@router.delete("/{seccion_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(
    seccion_id: int,
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("MODIFY_SERV"))] = None,  # type: ignore[assignment]
):
    service = SeccionService(session)
    if not service.delete(seccion_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sección no encontrada")
