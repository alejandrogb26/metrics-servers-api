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
    service = PermisoService(session)
    items, total = service.get_all(page=page, size=size)
    return PagedResponse(data=items, page=page, size=size, total=total)


@router.get("/{permiso_id}", response_model=PermisoRead)
def get_by_id(
    permiso_id: int,
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("AUDIT_USER"))] = None,  # type: ignore[assignment]
):
    service = PermisoService(session)
    permiso = service.get_by_id(permiso_id)
    if permiso is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Permiso no encontrado")
    return permiso
