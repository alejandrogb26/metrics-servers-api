"""
Servicio de autenticación.
Equivalente a AuthService.java.
"""

import logging

from fastapi import HTTPException, status
from sqlmodel import Session

from core.security import create_access_token
from core.config import get_settings
from models.common import LoginRequest, LoginResponse, SessionResponse
from models.usuario import UsuarioApp
from repositories.auth_repo import AuthRepository
from repositories.grupo_repo import GrupoRepository
from repositories.usuario_repo import UsuarioRepository
from services.ldap_service import LdapService
from services.minio_service import MinioService

log = logging.getLogger("api.auth")


class AuthService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._ldap = LdapService()
        self._minio = MinioService()

    def login(self, request: LoginRequest) -> LoginResponse:
        log.debug("LOGIN inicio username=%s", request.username)

        # 1. Validar campos obligatorios
        if not request.username or not request.password:
            log.debug("LOGIN error: username o password vacíos")
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Username y password son obligatorios",
            )

        # 2. Autenticar en LDAP
        ad_user = self._ldap.authenticate(request.username, request.password)
        if ad_user is None:
            log.debug("LOGIN ldap_fail username=%s", request.username)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Credenciales inválidas",
            )
        log.debug("LOGIN ldap_ok username=%s display_name=%s mail=%s groups_count=%d",
                  ad_user.sam_account_name, ad_user.display_name,
                  ad_user.mail, len(ad_user.member_of))

        # 3. Resolver grupo de AD
        grupo_repo = GrupoRepository(self._session)
        grupo = grupo_repo.find_by_any_dn(ad_user.member_of)
        if grupo is None:
            log.debug("LOGIN group_not_found username=%s member_of=%s",
                      ad_user.sam_account_name, ad_user.member_of)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="El usuario no pertenece a ningún grupo autorizado",
            )
        log.debug("LOGIN group_resolved username=%s grupo_id=%s grupo_nombre=%s superadmin=%s",
                  ad_user.sam_account_name, grupo.id, getattr(grupo, "nombre", "?"),
                  grupo.superadmin)

        # 4. Sincronizar registro local de usuario
        self._sync_usuario_app(ad_user.sam_account_name)

        # 5. Construir sesión con permisos
        auth_repo = AuthRepository(self._session)
        session_obj, foto_perfil = auth_repo.build_session(
            username=ad_user.sam_account_name,
            display_name=ad_user.display_name,
            mail=ad_user.mail,
            grupo=grupo,
        )
        log.debug("LOGIN session_built username=%s", ad_user.sam_account_name)

        # 6. Resolver URL de foto de perfil desde MinIO
        url_foto = self._minio.get_presigned_url(self._minio.BUCKET_USERS, foto_perfil)
        session_obj.url_foto = url_foto

        # 7. Generar JWT
        token = create_access_token(
            username=ad_user.sam_account_name,
            display_name=ad_user.display_name,
            mail=ad_user.mail,
            grupo_id=grupo.id,  # type: ignore[arg-type]
            superadmin=grupo.superadmin or False,
        )

        settings = get_settings()
        log.info("LOGIN ok username=%s grupo_id=%s superadmin=%s expires_in=%d",
                 ad_user.sam_account_name, grupo.id, grupo.superadmin,
                 settings.jwt_expiration_seconds)

        return LoginResponse(
            token=token,
            token_type="Bearer",
            expires_in=settings.jwt_expiration_seconds,
            session=session_obj.model_dump(by_alias=True),
        )

    def _sync_usuario_app(self, username: str) -> None:
        repo = UsuarioRepository(self._session)
        existing = repo.find_by_username(username)
        if existing is None:
            log.debug("LOGIN sync_usuario: creando registro local username=%s", username)
            nuevo = UsuarioApp(username=username, foto_perfil=None)
            repo.insert(nuevo)
        else:
            log.debug("LOGIN sync_usuario: registro ya existe username=%s", username)
