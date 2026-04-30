"""
Este archivo contiene las dependencias de FastAPI utilizadas para la autenticación
y autorización de los usuarios. Implementa una lógica de control de acceso mediante
JWT (JSON Web Tokens) y permisos, permitiendo verificar la validez de los tokens,
la autenticación del usuario, y la autorización en base a los permisos asignados al
usuario, similar a los filtros `TokenFilter` y `AuthorizationFilter` de Java.

Dependencias:
    - FastAPI: Para la gestión de dependencias y validación de solicitudes HTTP.
    - core.security.decode_token: Para decodificar y validar el JWT.
    - core.token_blocklist.is_revoked: Para verificar si el token ha sido revocado.
    - repositories.grupo_repo.GrupoRepository: Para recuperar los permisos del grupo desde la base de datos.
    - core.database: Para acceder a la base de datos y gestionar sesiones.
"""

from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.security import decode_token
from core.token_blocklist import is_revoked

# Configuración de autenticación mediante Bearer token
_bearer = HTTPBearer(auto_error=False)


# ─────────────────────────────────────────────────────────────────────────────
# Modelos internos de sesión de request
# ─────────────────────────────────────────────────────────────────────────────

class RequestUser:
    """
    Modelo que representa los datos del usuario autenticado extraídos del JWT.

    Atributos:
        username (str): Nombre de usuario extraído del campo "sub" del JWT.
        grupo_id (int | None): ID del grupo al que pertenece el usuario, si está disponible.
        superadmin (bool): Indica si el usuario es superadministrador.
        _global_perms (list[str] | None): Permisos globales del usuario.
        _section_perms (dict[int, list[str]] | None): Permisos por sección del usuario.
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        self.username: str = payload["sub"]
        self.grupo_id: int | None = payload.get("grupoId")
        self.superadmin: bool = bool(payload.get("superadmin", False))
        # Los permisos se cargarán bajo demanda desde la base de datos
        self._global_perms: list[str] | None = None
        self._section_perms: dict[int, list[str]] | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Dependencia: usuario autenticado (equivalente a TokenFilter)
# ─────────────────────────────────────────────────────────────────────────────

def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> RequestUser:
    """
    Valida el JWT proporcionado en el header Authorization y devuelve el usuario autenticado.

    Si el token es inválido, ha expirado o ha sido revocado, lanza un error HTTP 401.

    Parámetros:
        credentials (HTTPAuthorizationCredentials | None): El token Bearer extraído del header Authorization.

    Retorna:
        RequestUser: El usuario autenticado extraído del JWT.

    Lanza:
        HTTPException: Si el token no está presente o es inválido (401 Unauthorized).
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token no proporcionado",
        )

    # Decodificar el token y verificar su validez
    payload = decode_token(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado",
        )

    # Verificar si el token ha sido revocado
    jti = payload.get("jti")
    if jti and is_revoked(jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token revocado",
        )

    return RequestUser(payload)


# ─────────────────────────────────────────────────────────────────────────────
# Factory: require_permission (equivalente a @RequiresPermission + AuthorizationFilter)
# ─────────────────────────────────────────────────────────────────────────────

def require_permission(*required: str):
    """
    Factory que devuelve una dependencia de FastAPI que verifica que el usuario autenticado
    tenga al menos uno de los permisos indicados.

    Los superadministradores siempre tienen acceso completo. Si no se indica ningún permiso,
    se requiere únicamente estar autenticado.

    Parámetros:
        *required (str): Permisos requeridos para acceder a la ruta.

    Uso:
        @router.get("/", dependencies=[Depends(require_permission("AUDIT_SERV"))])
        o bien:
        current_user: Annotated[RequestUser, Depends(require_permission("AUDIT_SERV"))]
    """

    def _check(
        user: Annotated[RequestUser, Depends(get_current_user)],
        request: Request,
    ) -> RequestUser:
        # Superadmin tiene acceso completo sin importar los permisos
        if user.superadmin:
            return user

        # Si no se requiere ningún permiso, sólo se necesita estar autenticado
        if not required:
            return user

        # Cargar los permisos del grupo si no están disponibles en la sesión
        _load_perms_if_needed(user, request)

        global_perms: list[str] = user._global_perms or []
        section_perms: dict[int, list[str]] = user._section_perms or {}

        # Verificar si el usuario tiene permisos globales requeridos
        for perm in required:
            if perm in global_perms:
                return user

        # Verificar si el usuario tiene permisos por sección requeridos
        for section_list in section_perms.values():
            for perm in required:
                if perm in section_list:
                    return user

        # Si no tiene los permisos requeridos, se lanza un error HTTP 403
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permiso para realizar esta operación",
        )

    return _check


def _load_perms_if_needed(user: RequestUser, request: Request) -> None:
    """
    Carga los permisos del grupo desde la base de datos si aún no están cargados en el objeto `user`.

    Si el usuario no tiene un grupo asignado, se le asignan permisos vacíos.

    Parámetros:
        user (RequestUser): El usuario autenticado.
        request (Request): La solicitud actual (para acceso a la base de datos).
    """
    if user._global_perms is not None:
        return
    if user.grupo_id is None:
        user._global_perms = []
        user._section_perms = {}
        return

    # Importación local para evitar ciclo de dependencias
    from repositories.grupo_repo import GrupoRepository
    from core.database import engine
    from sqlmodel import Session

    try:
        # Obtener los permisos del grupo desde la base de datos
        with Session(engine) as session:
            repo = GrupoRepository(session)
            user._global_perms = repo.get_global_permission_names(user.grupo_id)
            user._section_perms = repo.get_section_permission_names(user.grupo_id)
    except Exception:
        # Si ocurre un error, se asignan permisos vacíos
        user._global_perms = []
        user._section_perms = {}