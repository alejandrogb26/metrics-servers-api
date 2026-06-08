"""
Servicio de autenticación.
Equivalente a AuthService.java.

Capa arquitectónica: Aplicación / Servicio.

Responsabilidades:
    - Orquestar el flujo completo de login en siete pasos: validación de campos,
      autenticación LDAP, resolución del grupo de AD, sincronización del registro
      local de usuario, construcción de la sesión con permisos, resolución de la
      URL de foto de perfil en MinIO y emisión del JWT.
    - Sincronizar el registro local `UsuarioApp` en el primer login de cada
      usuario (creación lazy del registro).
    - Ser el único punto del sistema que coordina LDAP, MariaDB y MinIO en una
      misma operación.

Qué NO debe contener este fichero:
    - Lógica de binding LDAP ni consultas al directorio. Eso pertenece a
      `services/ldap_service.py`.
    - Firma ni decodificación de tokens JWT. Eso pertenece a `core/security.py`.
    - Revocación de tokens (blocklist). Eso pertenece a `core/token_blocklist.py`
      y se gestiona en `routers/auth.py`.
    - Subida de ficheros a MinIO. Eso pertenece a `services/minio_service.py`.

Relaciones con otros módulos:
    - `core/config.py`            → `get_settings` para leer `jwt_expiration_seconds`.
    - `core/security.py`          → `create_access_token` para emitir el JWT.
    - `models/common.py`          → `LoginRequest`, `LoginResponse`, `SessionResponse`.
    - `models/usuario.py`         → `UsuarioApp` para la sincronización del registro local.
    - `repositories/auth_repo.py` → `AuthRepository.build_session` ensambla el
                                    objeto de sesión con permisos del grupo.
    - `repositories/grupo_repo.py`→ `GrupoRepository.find_by_any_dn` resuelve el
                                    grupo de AD a partir de los DNs del usuario.
    - `repositories/usuario_repo.py` → `UsuarioRepository` para crear o buscar el
                                       registro local del usuario.
    - `services/ldap_service.py`  → `LdapService.authenticate` valida credenciales
                                    y devuelve los atributos del usuario de AD.
    - `services/minio_service.py` → `MinioService.get_presigned_url` genera la URL
                                    firmada de la foto de perfil.
    - `routers/auth.py`           → instancia `AuthService(session)` en el handler
                                    de login.

Autor:
    Alejandro Gómez Blanco

Proyecto:
    Metrics Servers

Versión:
    1.0.0

Organización:
    Metrics Servers Project
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
    """
    Servicio que orquesta el flujo de login y la sincronización del usuario.

    Crea instancias de `LdapService` y `MinioService` internamente (no se
    inyectan por constructor) porque ambas son servicios de infraestructura
    sin estado y sin relación con la sesión de BD. Los repositorios de BD se
    crean dentro de cada método para compartir la misma sesión entre ellos.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._ldap = LdapService()
        self._minio = MinioService()

    def login(self, request: LoginRequest) -> LoginResponse:
        """
        Autentica al usuario y devuelve el JWT junto con los datos de sesión.

        Ejecuta siete pasos en secuencia. Cualquier fallo en los pasos 1-3
        eleva un `HTTPException` y aborta el flujo sin emitir token.

        Paso 1 — Validar campos obligatorios:
            Comprueba que `username` y `password` no están vacíos. Esta
            validación es redundante si `LoginRequest` tuviera `min_length=1`
            en sus campos, pero actúa como segunda línea de defensa.

        Paso 2 — Autenticar en LDAP:
            Llama a `LdapService.authenticate`, que realiza un bind LDAP con
            las credenciales recibidas. Si falla (credenciales incorrectas,
            usuario inexistente, LDAP caído), devuelve `None` → `HTTP 401`.
            El objeto `ad_user` devuelto contiene `sam_account_name`,
            `display_name`, `mail` y `member_of` (lista de DNs de grupos AD).

        Paso 3 — Resolver grupo de AD:
            Busca en la tabla `grupos` el grupo cuyo `dn` coincida con alguno
            de los DNs de `ad_user.member_of`. Si el usuario no pertenece a
            ningún grupo autorizado del sistema → `HTTP 401`.

        Paso 4 — Sincronizar registro local:
            Llama a `_sync_usuario_app` para garantizar que existe un registro
            en `usuarios_app` para este usuario. Si es el primer login, lo crea.

        Paso 5 — Construir sesión con permisos:
            `AuthRepository.build_session` carga los permisos del grupo (globales
            y por sección) y ensambla el `SessionResponse`. Devuelve también
            el nombre del fichero de foto de perfil (`foto_perfil`) para que
            este servicio pueda resolver la URL en el paso 6.

        Paso 6 — Resolver URL de foto de perfil:
            Genera una URL presignada de MinIO a partir del nombre de fichero
            devuelto en el paso 5. Si el usuario no tiene foto (`foto_perfil`
            es `None`), `get_presigned_url` devuelve `None` y `url_foto`
            queda como `None` en la sesión.

        Paso 7 — Generar JWT:
            `create_access_token` firma el JWT con HMAC-SHA256 incluyendo
            `sub` (username), `display_name`, `mail`, `grupo_id` y `superadmin`.
            `grupo.superadmin or False` convierte `None` (registros legados) en
            `False` para evitar incluir `None` en el claim del token.

        La sesión se serializa con `model_dump(by_alias=True)` para producir
        claves camelCase en el JSON de respuesta, coherente con el contrato de
        los clientes Flutter y Swing.

        Args:
            request: DTO `LoginRequest` con `username` y `password`.

        Retorna:
            `LoginResponse` con `token` (JWT Bearer), `tokenType`, `expiresIn`
            (segundos) y `session` (datos del usuario y sus permisos).

        Errores HTTP:
            422 Unprocessable — username o password vacíos.
            401 Unauthorized  — credenciales LDAP incorrectas, o usuario sin
                                grupo autorizado en el sistema.
        """
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
        """
        Garantiza que existe un registro local `UsuarioApp` para el usuario.

        Se llama en cada login. Si el usuario ya tiene registro en `usuarios_app`
        (logins previos), no hace nada. Si es el primer login, crea el registro
        con `foto_perfil=None` (sin foto inicial).

        Identifica al usuario por `username` (`sam_account_name`), no por
        `ad_object_id`. Esto implica que si el usuario cambia su nombre de cuenta
        en AD, se crearía un nuevo registro local y el anterior (con la foto
        de perfil) quedaría huérfano.

        El campo `ad_object_id` no se rellena en la creación: `UsuarioApp` se
        crea solo con `username`. La sincronización es mínima por diseño: solo
        establece que el usuario existe, sin sincronizar todos los atributos LDAP.

        Args:
            username: `sam_account_name` del usuario autenticado.
        """
        repo = UsuarioRepository(self._session)
        existing = repo.find_by_username(username)
        if existing is None:
            log.debug("LOGIN sync_usuario: creando registro local username=%s", username)
            nuevo = UsuarioApp(username=username, foto_perfil=None)
            repo.insert(nuevo)
        else:
            log.debug("LOGIN sync_usuario: registro ya existe username=%s", username)
