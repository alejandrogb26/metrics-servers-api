from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session

from core.database import get_session
from core.dependencies import RequestUser, require_permission
from models.grupo import GrupoRead
from models.permission_map import PermissionMap
from services.grupo_service import GrupoPermisosService, GrupoService


class PermisoPatch(BaseModel):
    add: list[int] | None = None
    remove: list[int] | None = None


router = APIRouter(prefix="/grupos/{grupo_id}/permisos", tags=["Grupos – Permisos"])


@router.put("", response_model=GrupoRead)
def replace_all(
    grupo_id: int,
    permisos: PermissionMap[int],
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("MODIFY_USER"))] = None,  # type: ignore[assignment]
):
    """Reemplaza todos los permisos del grupo (global + secciones)."""
    svc = GrupoPermisosService(session)
    if not svc.replace_all(grupo_id, permisos):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grupo no encontrado")
    return GrupoService(session).get_by_id(grupo_id)


@router.patch("/global", response_model=GrupoRead)
def patch_global(
    grupo_id: int,
    req: PermisoPatch,
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("MODIFY_USER"))] = None,  # type: ignore[assignment]
):
    """Modifica permisos globales del grupo de forma incremental (add/remove)."""
    svc = GrupoPermisosService(session)
    if not svc.patch_global(grupo_id, req.add, req.remove):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grupo no encontrado")
    return GrupoService(session).get_by_id(grupo_id)


@router.put("/secciones/{seccion_id}", response_model=GrupoRead)
def replace_seccion(
    grupo_id: int,
    seccion_id: int,
    permiso_ids: list[int],
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("MODIFY_USER"))] = None,  # type: ignore[assignment]
):
    """Reemplaza todos los permisos de una sección para el grupo."""
    svc = GrupoPermisosService(session)
    if not svc.replace_seccion(grupo_id, seccion_id, permiso_ids):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grupo no encontrado")
    return GrupoService(session).get_by_id(grupo_id)


@router.patch("/secciones/{seccion_id}", response_model=GrupoRead)
def patch_seccion(
    grupo_id: int,
    seccion_id: int,
    req: PermisoPatch,
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("MODIFY_USER"))] = None,  # type: ignore[assignment]
):
    """Modifica permisos de una sección para el grupo de forma incremental."""
    svc = GrupoPermisosService(session)
    if not svc.patch_seccion(grupo_id, seccion_id, req.add, req.remove):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grupo no encontrado")
    return GrupoService(session).get_by_id(grupo_id)
