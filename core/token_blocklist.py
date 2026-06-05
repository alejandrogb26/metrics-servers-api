"""
Módulo de blocklist de tokens JWT mediante Redis.

Capa arquitectónica: Infraestructura / Seguridad transversal.

Responsabilidades:
    - Revocar tokens JWT individuales en el momento del logout del usuario,
      almacenando su JTI (JWT ID) en Redis con un TTL igual al tiempo de vida
      residual del token (`revoke`).
    - Comprobar en cada petición autenticada si el JTI del token entrante ha
      sido revocado (`is_revoked`), cerrando la brecha de seguridad inherente
      a la naturaleza stateless de JWT.
    - Gestionar de forma robusta la indisponibilidad de Redis: si el servicio
      no está accesible, la aplicación sigue funcionando con degradación
      controlada en lugar de fallar completamente.

Qué NO debe contener este fichero:
    - Lógica de autenticación ni de autorización. Eso pertenece a
      `core/dependencies.py` y `services/auth_service.py`.
    - Cualquier otro uso de Redis. Si en el futuro se necesita Redis para
      otros propósitos (caché, colas, sesiones), deben usarse clientes y
      módulos separados para mantener la separación de responsabilidades.

Relaciones con otros módulos:
    - `core/config.py`         → proporciona `redis_url` con la dirección y
                                 credenciales del servidor Redis.
    - `core/security.py`       → el claim `jti` del token (UUID4) es la clave
                                 primaria de la blocklist; `exp` es el timestamp
                                 de expiración usado para calcular el TTL.
    - `core/dependencies.py`   → llama a `is_revoked(jti)` en cada petición
                                 autenticada, después de verificar la firma JWT.
    - `routers/auth.py`        → llama a `revoke(jti, exp)` en el endpoint de
                                 logout para invalidar el token activo del usuario.

Por qué es necesaria la blocklist:
    Los tokens JWT son stateless y criptográficamente autónomos: una vez emitidos,
    son válidos hasta su expiración aunque el usuario haga logout. Sin una
    blocklist, el logout sería puramente cosmético en el cliente (borrar el token
    localmente) pero el token seguiría siendo aceptado por la API hasta expirar.
    La blocklist en Redis corrige este comportamiento almacenando los JTI de los
    tokens invalidados y consultándola en cada petición.

Política de degradación ante fallos de Redis (fail-open):
    Tanto `revoke` como `is_revoked` están diseñados para no interrumpir el flujo
    de la aplicación si Redis no está disponible:
    - Si Redis cae, los tokens revocados podrían ser reutilizados hasta su expiración
      natural. Este es el coste asumido de priorizar la disponibilidad sobre la
      revocación estricta. Ver sección de observaciones para las implicaciones.
    - La alternativa fail-closed (denegar toda petición si Redis falla) haría la
      aplicación completamente inoperativa ante una caída de Redis, lo que se
      consideró un riesgo mayor para este caso de uso.
"""

import logging
from datetime import datetime, timezone

import redis

from core.config import get_settings

logger = logging.getLogger(__name__)

# Prefijo de namespace para todas las claves de la blocklist en Redis.
# Evita colisiones con otras claves si la misma instancia de Redis se comparte
# con otros servicios o propósitos en el futuro.
_KEY_PREFIX = "jwt_blocklist:"

# Cliente Redis singleton a nivel de módulo. Se inicializa de forma lazy en la
# primera llamada a `_get_client()` y se reutiliza en todas las llamadas posteriores.
_client: redis.Redis | None = None  # type: ignore[type-arg]

# Flag de un solo uso que garantiza que el intento de conexión a Redis se realiza
# exactamente una vez por proceso. Si la conexión falla, las llamadas posteriores
# retornan `None` inmediatamente sin reintentar. Esto evita que cada petición HTTP
# incurra en el coste del timeout de conexión (2 segundos) si Redis está caído.
# Contrapartida: si Redis se recupera, la aplicación no reconectará hasta reiniciarse.
_init_attempted = False


def _get_client() -> "redis.Redis | None":  # type: ignore[type-arg]
    """
    Devuelve el cliente Redis singleton, inicializándolo en la primera llamada.

    Implementa el patrón de inicialización lazy con intento único: si la conexión
    a Redis falla, el flag `_init_attempted` queda a `True` y todas las llamadas
    posteriores retornan `None` sin intentar reconectar. Esto es una decisión de
    rendimiento deliberada para no degradar la latencia de cada petición HTTP ante
    una caída de Redis.

    La conexión se verifica con `c.ping()` antes de aceptar el cliente como válido.
    Si `ping()` falla (Redis no responde), se loguea un WARNING descriptivo que
    informa al operador de que la revocación de tokens no está activa.

    Timeouts configurados:
        - `socket_connect_timeout=2`: máximo 2 segundos para establecer la
          conexión TCP con Redis. Sin este límite, un Redis inaccesible podría
          bloquear el arranque de la aplicación indefinidamente.
        - `socket_timeout=2`: máximo 2 segundos para operaciones individuales
          (GET, SET, EXISTS). Previene que una operación Redis lenta bloquee el
          hilo que atiende una petición HTTP.

    `decode_responses=True` hace que el cliente decodifique automáticamente las
    respuestas de bytes a cadenas Python, necesario para que `exists()` y `setex()`
    trabajen con los JTI en formato string sin conversiones manuales.

    Retorna:
        redis.Redis conectado y verificado, o `None` si la conexión falló o aún
        no se ha intentado (antes de la primera llamada).
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
        c.ping()
        _client = c
        logger.info("Blocklist de tokens: conectado a Redis en %s", settings.redis_url)
    except Exception as exc:
        logger.warning(
            "Blocklist de tokens no disponible (Redis inaccesible): %s. "
            "Los tokens revocados no serán bloqueados hasta que Redis esté disponible.",
            exc,
        )
    return _client


def revoke(jti: str, exp: int) -> None:
    """
    Añade el JTI del token a la blocklist Redis con TTL igual al tiempo de vida
    residual del token.

    Mecanismo de auto-limpieza:
        La clave se almacena en Redis con `SETEX`, que asocia un TTL en segundos.
        Cuando el token habría expirado de forma natural (según su claim `exp`),
        la entrada en Redis expira y se elimina automáticamente. Esto garantiza
        que la blocklist no crece indefinidamente: cada entrada ocupa espacio en
        Redis exactamente el tiempo necesario y no más.

    Cálculo del TTL:
        `ttl = exp - ahora_UTC_en_segundos`

        Si el TTL resulta <= 0, el token ya había expirado en el momento del
        logout y no se almacena nada: un token expirado es inofensivo porque
        `decode_token` en `core/security.py` lo rechazaría por su claim `exp`
        independientemente de la blocklist.

    Comportamiento ante fallos de Redis:
        Si Redis no está disponible (`client is None`) o lanza `RedisError`
        durante el `setex`, se loguea el incidente (WARNING o ERROR según el caso)
        pero la función retorna sin excepción. El endpoint de logout completará
        su flujo normal desde la perspectiva del cliente. El token seguirá siendo
        válido hasta su expiración natural, lo que representa una ventana de
        seguridad temporal.

    Args:
        jti: JWT ID del token a revocar. Corresponde al claim `jti` del payload
             JWT, generado como UUID4 en `core/security.py`.
        exp: Timestamp Unix (segundos UTC) de expiración del token. Corresponde
             al claim `exp` del payload JWT. Debe ser un entero; si se pasa el
             valor flotante de `datetime.timestamp()`, truncar con `int()`.
    """
    client = _get_client()
    if client is None:
        logger.warning("Logout registrado localmente pero no en Redis (JTI=%s).", jti)
        return

    # Tiempo restante hasta la expiración natural del token.
    ttl = exp - int(datetime.now(tz=timezone.utc).timestamp())
    if ttl <= 0:
        return  # El token ya ha expirado, no hay nada que revocar

    try:
        client.setex(f"{_KEY_PREFIX}{jti}", ttl, "1")
    except redis.RedisError as exc:
        logger.error("Error al revocar token en Redis (JTI=%s): %s", jti, exc)


def is_revoked(jti: str) -> bool:
    """
    Comprueba si el JTI del token está presente en la blocklist Redis.

    Se invoca desde `core/dependencies.py` en cada petición autenticada, después
    de que `decode_token` haya verificado la firma y la expiración del token. Solo
    si las tres capas pasan (firma válida + no expirado + no revocado) se considera
    el token legítimo.

    Comportamiento ante fallos de Redis (fail-open):
        Si Redis no está disponible o lanza `RedisError` durante el `exists`,
        la función devuelve `False` (no revocado). Esto implementa una política
        fail-open: ante la duda, se permite el acceso.

        La alternativa fail-closed (devolver `True` si Redis falla) haría que
        ningún usuario autenticado pudiera usar la API mientras Redis estuviera
        caído. Dado que los tokens tienen una ventana de validez limitada por
        `jwt_expiration_seconds` (por defecto 8 horas), la exposición real de
        un token revocado que se cuele por un fallo de Redis es acotada en el
        tiempo.

    Args:
        jti: JWT ID del token a verificar. Debe ser el valor exacto del claim
             `jti` del payload decodificado.

    Retorna:
        True  si el JTI está en la blocklist (token revocado, acceso denegado).
        False si el JTI no está en la blocklist, o si Redis no está disponible
              (fail-open).
    """
    client = _get_client()
    if client is None:
        # Redis no disponible: fail-open, se permite el acceso.
        return False

    try:
        return bool(client.exists(f"{_KEY_PREFIX}{jti}"))
    except redis.RedisError as exc:
        logger.error("Error al comprobar blocklist en Redis (JTI=%s): %s", jti, exc)
        return False  # Fail-open: si Redis falla en tiempo de operación, no bloqueamos.
