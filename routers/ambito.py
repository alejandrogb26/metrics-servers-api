from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session

from core.database import get_session
from core.dependencies import RequestUser, require_permission
from models.ambito import AmbitoRead
from models.common import PagedResponse
from services.ambito_service import AmbitoService

router = APIRouter(prefix="/ambitos", tags=["Ámbitos"])


@router.get("", response_model=PagedResponse[AmbitoRead])
def get_all(
    page: int = Query(default=0, ge=0, description="Página (base 0)"),
    size: int = Query(default=50, ge=1, le=100, description="Elementos por página"),
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("AUDIT_SYS"))] = None,  # type: ignore[assignment]
):
    service = AmbitoService(session)
    items, total = service.get_all(page=page, size=size)
    return PagedResponse(data=items, page=page, size=size, total=total)


@router.get("/{ambito_id}", response_model=AmbitoRead)
def get_by_id(
    ambito_id: int,
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("AUDIT_SYS"))] = None,  # type: ignore[assignment]
):
    service = AmbitoService(session)
    ambito = service.get_by_id(ambito_id)
    if ambito is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ámbito no encontrado")
    return ambito
