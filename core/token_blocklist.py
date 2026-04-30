"""
Este archivo implementa una lista de bloqueo (blocklist) para tokens JWT utilizando Redis.
Cuando un usuario hace logout, el **JTI** (Identificador de Token JWT) del token se almacena
en Redis con un tiempo de vida (TTL) igual al tiempo restante hasta la expiración del token.
De esta manera, cualquier solicitud posterior con ese **JTI** será rechazada, aunque el token
sea criptográficamente válido.

Dependencias:
    - redis: Para interactuar con el sistema de almacenamiento Redis.
    - core.config.get_settings: Para obtener la configuración de Redis desde el archivo de configuración.
"""

import logging
from datetime import datetime, timezone

import redis

from core.config import get_settings

# Configuración de logging
logger = logging.getLogger(__name__)

# Prefijo para las claves de la blocklist de tokens en Redis
_KEY_PREFIX = "jwt_blocklist:"
_client: redis.Redis | None = None  # type: ignore[type-arg]
_init_attempted = False  # Indicador para asegurar que solo intentamos inicializar Redis una vez


def _get_client() -> "redis.Redis | None":  # type: ignore[type-arg]
    """
    Obtiene el cliente de Redis configurado. Si Redis no está disponible, se intenta la conexión
    una sola vez y se registra un error en caso de fallo.

    Retorna:
        redis.Redis | None: El cliente de Redis configurado o `None` si no se pudo conectar.
    """
    global _client, _init_attempted
    if _init_attempted:
        return _client
    _init_attempted = True
    try:
        settings = get_settings()
        c: redis.Redis = redis.from_url(  # type: ignore[type-arg]
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        c.ping()  # Verificar la conexión con Redis
        _client = c
        logger.info("Blocklist de tokens: conectado a Redis en %s", settings.redis_url)
    except Exception as exc:
        # Si no se puede conectar a Redis, se registra una advertencia
        logger.warning(
            "Blocklist de tokens no disponible (Redis inaccesible): %s. "
            "Los tokens revocados no serán bloqueados hasta que Redis esté disponible.",
            exc,
        )
    return _client


def revoke(jti: str, exp: int) -> None:
    """
    Añade el JTI (Identificador del Token JWT) al blocklist hasta que el token expire naturalmente.
    El JTI se almacena en Redis con un tiempo de vida (TTL) igual al tiempo restante hasta la
    expiración del token.

    Parámetros:
        jti (str): El identificador único del token (JTI).
        exp (int): El timestamp de expiración del token en segundos.
    """
    client = _get_client()
    if client is None:
        # Si Redis no está disponible, se registra un warning localmente sin interrumpir el flujo
        logger.warning("Logout registrado localmente pero no en Redis (JTI=%s).", jti)
        return

    # Calculamos el TTL (tiempo restante hasta la expiración del token)
    ttl = exp - int(datetime.now(tz=timezone.utc).timestamp())
    if ttl <= 0:
        return  # El token ya ha expirado, no hay nada que revocar

    try:
        # Almacenamos el JTI en Redis con el TTL calculado
        client.setex(f"{_KEY_PREFIX}{jti}", ttl, "1")
    except redis.RedisError as exc:
        # Si ocurre un error en Redis, se registra un error
        logger.error("Error al revocar token en Redis (JTI=%s): %s", jti, exc)


def is_revoked(jti: str) -> bool:
    """
    Verifica si el JTI está presente en la blocklist (es decir, si el token ha sido revocado).

    Parámetros:
        jti (str): El identificador único del token (JTI).

    Retorna:
        bool: `True` si el token ha sido revocado (está en la blocklist), `False` si no.
    """
    client = _get_client()
    if client is None:
        # Si Redis no está disponible, consideramos el token como no revocado (fail-open)
        return False

    try:
        # Verificamos si el JTI está en la blocklist de Redis
        return bool(client.exists(f"{_KEY_PREFIX}{jti}"))
    except redis.RedisError as exc:
        # Si ocurre un error en Redis, se registra un error y se devuelve False (fail-open)
        logger.error("Error al comprobar blocklist en Redis (JTI=%s): %s", jti, exc)
        return False  # Fail-open: Si Redis no está disponible, no bloqueamos el token