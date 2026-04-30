from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlmodel import Session

from core.database import get_session
from core.dependencies import RequestUser, get_current_user
from models.common import UploadResult
from services.usuario_service import UsuarioService

router = APIRouter(prefix="/usuario", tags=["Usuarios"])


@router.post("/foto", response_model=UploadResult)
async def upload_foto(
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    current_user: Annotated[RequestUser, Depends(get_current_user)] = None,  # type: ignore[assignment]
):
    """
    Sube o reemplaza la foto de perfil del usuario autenticado.
    El usuario se identifica por el JWT.
    """
    if not file or not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Archivo no proporcionado",
        )

    username = current_user.username
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token no válido",
        )

    data = await file.read()
    svc = UsuarioService(session)
    nombre, url_foto = svc.update_foto_perfil(username, data, file.filename)

    return UploadResult(nombre_archivo=nombre, url_foto=url_foto)
