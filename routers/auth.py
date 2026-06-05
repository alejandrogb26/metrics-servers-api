import logging
from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from core.database import get_session
from core.security import decode_token
from core.token_blocklist import revoke
from models.common import LoginRequest, LoginResponse
from services.auth_service import AuthService

log = logging.getLogger("api.auth")

router = APIRouter(prefix="/auth", tags=["Auth"])

_bearer = HTTPBearer(auto_error=False)


@router.post("/login", response_model=LoginResponse)
def login(request: LoginRequest, session: Session = Depends(get_session)):
    """
    Autentica al usuario contra Active Directory y devuelve un JWT junto con la sesión.
    """
    service = AuthService(session)
    return service.login(request)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
):
    """
    Revoca el JWT actual añadiendo su JTI al blocklist de Redis.
    Si no se envía token, la petición se ignora (idempotente).
    """
    if credentials is None:
        log.debug("LOGOUT sin token (ignorado)")
        return
    payload = decode_token(credentials.credentials)
    if payload is None:
        log.debug("LOGOUT token ya inválido/expirado (nada que revocar)")
        return
    jti = payload.get("jti")
    exp = payload.get("exp")
    if jti and exp:
        revoke(jti, int(exp))
        log.info("LOGOUT ok username=%s jti=%s", payload.get("sub"), jti)
