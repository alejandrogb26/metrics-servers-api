"""
Router HTTP para el ciclo de vida de la sesión de usuario: login y logout.

Capa arquitectónica: Presentación / Routing HTTP (autenticación).

Responsabilidades:
    - Exponer el endpoint `POST /auth/login` para autenticar usuarios contra
      Active Directory y emitir un JWT junto con los datos de sesión.
    - Exponer el endpoint `POST /auth/logout` para revocar el JWT activo
      añadiendo su JTI al blocklist de Redis.
    - Delegar toda la lógica de autenticación en `AuthService`.

Qué NO debe contener este fichero:
    - Lógica LDAP ni validación de credenciales. Eso pertenece a
      `services/ldap_service.py` (invocado desde `AuthService`).
    - Construcción de la sesión ni carga de permisos. Eso pertenece a
      `services/auth_service.py` y `repositories/auth_repo.py`.
    - Firma ni decodificación de tokens. Eso pertenece a `core/security.py`.
    - Gestión del blocklist de Redis. Eso pertenece a `core/token_blocklist.py`.

Contrato HTTP de este router:

    ┌────────────────────────┬────────────────────┬─────────────────────────────┐
    │ Método + Ruta          │ Autenticación      │ Respuesta exitosa           │
    ├────────────────────────┼────────────────────┼─────────────────────────────┤
    │ POST /auth/login       │ Ninguna (público)  │ 200 LoginResponse           │
    │ POST /auth/logout      │ Opcional (Bearer)  │ 204 No Content              │
    └────────────────────────┴────────────────────┴─────────────────────────────┘

    El endpoint de logout es idempotente: si no se envía token, o si el token
    ya está expirado o es inválido, la petición se acepta y devuelve 204 sin
    hacer nada.

Relaciones con otros módulos:
    - `core/database.py`         → `get_session` proporciona la `Session` de BD.
    - `core/security.py`         → `decode_token` para decodificar el JWT en logout.
    - `core/token_blocklist.py`  → `revoke` para añadir el JTI al blocklist de Redis.
    - `models/common.py`         → `LoginRequest` (body) y `LoginResponse` (respuesta).
    - `services/auth_service.py` → delega toda la lógica de login.
    - `main.py`                  → registra este router con `app.include_router`.

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

# HTTPBearer con auto_error=False: si la cabecera Authorization está ausente o
# mal formada, FastAPI inyecta None en lugar de elevar un 403 automático. Esto
# permite que el endpoint de logout decida explícitamente qué hacer cuando no
# hay token (ignorar la petición en lugar de rechazarla).
_bearer = HTTPBearer(auto_error=False)


@router.post("/login", response_model=LoginResponse)
def login(request: LoginRequest, session: Session = Depends(get_session)):
    """
    Autentica al usuario contra Active Directory y devuelve un JWT junto con la sesión.

    Endpoint público: no requiere token previo. Recibe las credenciales del
    usuario en el cuerpo de la petición (`LoginRequest`), las valida contra LDAP
    a través de `AuthService` y, si son correctas, devuelve:
      - El JWT firmado con HMAC-SHA256 (HS256).
      - Los datos de sesión del usuario: grupo, permisos globales y por sección.

    La contraseña del usuario nunca se almacena en la BD ni se registra en los
    logs. El flujo es: LDAP bind → construcción de sesión → emisión de JWT.

    Args:
        request: Body `LoginRequest` con `username` y `password` del usuario.
        session: Sesión de base de datos inyectada por `get_session`, necesaria
                 para cargar los datos del grupo y permisos del usuario.

    Retorna:
        `LoginResponse` con el JWT (`token`), el tipo de token (`tokenType`:
        "Bearer"), y los datos de sesión (`session`: `SessionResponse`).

    Errores HTTP:
        401 Unauthorized — credenciales LDAP incorrectas o usuario no encontrado.
        500 Internal     — error de conectividad con LDAP o BD.
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

    Estrategia de revocación (stateless JWT → blocklist):
        Los JWT son stateless por diseño: una vez emitidos, son válidos hasta su
        expiración sin consultar el servidor. Para implementar logout real, el
        JTI (JWT ID) del token se añade a Redis con un TTL igual al tiempo
        restante hasta la expiración (`exp`). A partir de ese momento,
        `core/dependencies.py` rechaza cualquier petición que presente ese JTI.

    Flujo de la función:
        1. Sin cabecera Authorization (o mal formada): `credentials` es `None`
           por el `auto_error=False` de `_bearer`. Se retorna sin hacer nada.
        2. Token inválido o ya expirado: `decode_token` devuelve `None`. Se
           retorna sin hacer nada (ya no es necesario revocar).
        3. Token válido con `jti` y `exp`: se llama a `revoke(jti, int(exp))`
           para añadir el JTI al blocklist de Redis.

    El endpoint siempre devuelve `204 No Content`, independientemente de si el
    token existía, era válido o ya estaba revocado. Esto es intencional: el
    cliente no necesita distinguir el motivo, solo sabe que ya no hay sesión
    activa desde su perspectiva.

    Seguridad:
        Un token que no contenga `jti` o `exp` en el payload (condición `if jti
        and exp:`) no se añade al blocklist y el logout se silencia sin error.
        Si en algún momento se emiten tokens sin `jti`, estos no serán revocables
        por este endpoint pero seguirán siendo válidos hasta su expiración natural.

    Args:
        credentials: Credenciales Bearer extraídas de la cabecera `Authorization`,
                     o `None` si no se envió la cabecera o no tiene formato Bearer.
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
