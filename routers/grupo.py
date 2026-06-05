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
    svc = GrupoService(session)
    items, total = svc.get_all(page=page, size=size)
    return PagedResponse(data=items, page=page, size=size, total=total)


@router.get("/{grupo_id}", response_model=GrupoRead)
def get_by_id(
    grupo_id: int,
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("AUDIT_USER"))] = None,  # type: ignore[assignment]
):
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
    svc = GrupoService(session)
    return svc.create_bulk(grupos)


@router.patch("/{grupo_id}", response_model=GrupoRead)
def update(
    grupo_id: int,
    patch: GrupoPatch,
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("MODIFY_USER"))] = None,  # type: ignore[assignment]
):
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
    svc = GrupoService(session)
    if not svc.delete(grupo_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grupo no encontrado")


