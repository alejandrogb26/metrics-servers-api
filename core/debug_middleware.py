"""
Middleware de logging HTTP con sanitización de datos sensibles.

Capa arquitectónica: Infraestructura / Observabilidad.

Responsabilidades:
    - Interceptar todas las peticiones HTTP entrantes y registrar información
      de acceso en dos niveles de detalle controlados por la configuración
      `APP_DEBUG` (que determina el nivel del logger `api.http`):

      · Modo INFO  (APP_DEBUG=false, producción):
            Una línea por petición con método, ruta, código de estado, latencia
            en milisegundos e IP del cliente. Formato apto para ingestión en
            sistemas de log centralizados (Loki, ELK, etc.).

      · Modo DEBUG (APP_DEBUG=true, desarrollo/diagnóstico):
            Cabeceras sanitizadas, query params, path params, body de la petición
            (JSON o form-urlencoded), body de la respuesta JSON, usuario autenticado
            extraído del JWT y stacktraces de errores.

    - Garantizar que ciertos datos nunca aparecen en texto claro en los logs,
      independientemente del nivel activo:
        · Cabecera `Authorization`  → sustituida por "Bearer ***REDACTED***".
        · Cabecera `Cookie`, `x-api-key`, `x-auth-token` → mismo tratamiento.
        · Campos sensibles en el body (password, secret, token, etc.) → "***REDACTED***".
        · El token JWT completo → nunca se vuelca; solo se extrae el campo `sub`
          (username) y `jti` para identificar al usuario en los logs.

Qué NO debe contener este fichero:
    - Lógica de negocio ni validación de autorización.
    - Configuración del nivel de logging (eso pertenece a `core/logging_config.py`).
    - Manejo de errores de la aplicación (eso pertenece a `exceptions/handlers.py`).

Relaciones con otros módulos:
    - `core/logging_config.py` → configura el nivel del logger `api.http`
      en función de `app_debug`. Este middleware solo comprueba ese nivel.
    - `core/security.py` → `_extract_username` importa `decode_token` de forma
      diferida (lazy import) para obtener el `sub` del JWT sin fallar si el
      token es inválido o está ausente.
    - `main.py` → registra este middleware condicionalmente cuando `app_debug=True`.

Advertencia de seguridad:
    El modo DEBUG puede exponer datos sensibles en los logs. Activar únicamente
    en entornos de desarrollo o diagnóstico controlados, nunca en producción.
    Ver también el campo `app_debug` en `core/config.py`.
"""

import json
import logging
import time
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Logger dedicado para el tráfico HTTP. Su nivel se configura externamente en
# `core/logging_config.py` según el valor de `APP_DEBUG`. El nombre jerárquico
# `api.http` permite filtrar o dirigir estos logs a un destino distinto del
# logger raíz sin afectar al resto de la aplicación.
log = logging.getLogger("api.http")

# Cabeceras HTTP que contienen credenciales o tokens y que nunca deben aparecer
# en texto claro en los logs. Se usa `frozenset` por su O(1) en lookups y porque
# el conjunto es inmutable (no debe modificarse en runtime).
_REDACT_HEADERS = frozenset({"authorization", "cookie", "x-api-key", "x-auth-token"})

# Claves de campos JSON/form cuyo valor debe ocultarse en logs. La comparación se
# hace en minúsculas en `_sanitize_dict`, por lo que "Password" o "PASSWORD"
# también quedan cubiertos.
_REDACT_BODY_KEYS = frozenset({"password", "passwd", "secret", "token", "credential", "access_token"})

# Solo se intenta loguear el body cuando el Content-Type indica que es texto
# estructurado. Otros tipos (multipart, octet-stream, etc.) se omiten para
# evitar volcar binarios en los logs.
_LOG_BODY_CONTENT_TYPES = ("application/json", "application/x-www-form-urlencoded")

# Límite de caracteres para el volcado de bodies en los logs. Evita que respuestas
# o peticiones muy grandes (p. ej., listas paginadas largas) generen líneas de log
# inmanejables que saturen el sistema de logging o dificulten la lectura.
_MAX_BODY_LOG_CHARS = 4096


def _sanitize_headers(headers: dict[str, str]) -> dict[str, str]:
    """
    Devuelve una copia del diccionario de cabeceras con los valores sensibles
    sustituidos por el literal "Bearer ***REDACTED***".

    La sustitución es uniforme para todas las cabeceras en `_REDACT_HEADERS`,
    independientemente de su valor original, para evitar fugas accidentales de
    tokens o cookies de sesión en los logs.

    Args:
        headers: Diccionario de cabeceras HTTP tal como lo expone Starlette.

    Retorna:
        Nuevo diccionario con los mismos pares clave-valor salvo los sensibles.
    """
    return {
        k: ("Bearer ***REDACTED***" if k.lower() in _REDACT_HEADERS else v)
        for k, v in headers.items()
    }


def _sanitize_dict(data: dict) -> dict:
    """
    Devuelve una copia superficial del diccionario con los valores de claves
    sensibles sustituidos por "***REDACTED***".

    Limitación conocida: la sanitización es solo de un nivel de profundidad
    (shallow). Si el body tiene objetos anidados que contienen campos sensibles
    (p. ej., `{"user": {"password": "abc"}}`), el campo anidado NO quedará
    redactado. Ver sección de observaciones en el módulo.

    Args:
        data: Diccionario parseado del body JSON o form de la petición.

    Retorna:
        Nuevo diccionario con valores sensibles enmascarados.
    """
    return {
        k: ("***REDACTED***" if k.lower() in _REDACT_BODY_KEYS else v)
        for k, v in data.items()
    }


def _extract_username(request: Request) -> str | None:
    """
    Extrae el nombre de usuario (`sub`) del token JWT presente en la cabecera
    `Authorization`, sin propagar excepciones.

    Se usa exclusivamente para enriquecer los logs con el usuario que realiza la
    petición, facilitando la correlación de tráfico en diagnósticos. No tiene
    ningún efecto sobre la autenticación ni la autorización de la petición.

    El import de `decode_token` es diferido (dentro de la función) para evitar
    importaciones circulares entre `core/debug_middleware.py` y `core/security.py`
    en el momento de la carga del módulo.

    Todas las excepciones se capturan silenciosamente: si el token está ausente,
    malformado, expirado o la firma es inválida, la función simplemente devuelve
    `None`. El logging nunca debe interrumpir el flujo normal de una petición.

    Args:
        request: Objeto `Request` de Starlette con las cabeceras de la petición.

    Retorna:
        El valor del campo `sub` del payload JWT (normalmente el username),
        o `None` si no hay token válido o si ocurre cualquier error.
    """
    try:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            from core.security import decode_token
            payload = decode_token(auth[7:])
            if payload:
                return payload.get("sub")
    except Exception:
        pass
    return None


def _truncate(text: str, limit: int = _MAX_BODY_LOG_CHARS) -> str:
    """
    Trunca el texto a `limit` caracteres añadiendo un sufijo informativo si se
    supera el límite.

    El sufijo indica el tamaño total original para que el lector de logs sepa
    que el contenido está incompleto y pueda buscar el body completo por otros
    medios si es necesario.

    Args:
        text:  Cadena a truncar (típicamente el body serializado como JSON).
        limit: Número máximo de caracteres permitidos. Por defecto `_MAX_BODY_LOG_CHARS`.

    Retorna:
        La cadena original si no supera el límite, o los primeros `limit`
        caracteres seguidos de "… [truncado, total N chars]".
    """
    if len(text) <= limit:
        return text
    return text[:limit] + f"… [truncado, total {len(text)} chars]"


class DebugLoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware Starlette/FastAPI de logging HTTP con dos modos de operación.

    Hereda de `BaseHTTPMiddleware`, que implementa el patrón middleware de
    Starlette envolviendo cada petición con una llamada a `dispatch`. Esta clase
    se registra en `main.py` mediante `app.add_middleware(DebugLoggingMiddleware)`.

    Modo INFO (producción):
        Registra una línea por petición con: método, ruta, status code, latencia,
        IP y usuario ("anon" en producción porque la extracción del JWT solo se
        realiza en modo DEBUG por razones de rendimiento).

    Modo DEBUG (desarrollo):
        Además del log INFO, registra cabeceras sanitizadas, params, body de
        petición y body de respuesta. El body de respuesta requiere consumir y
        reconstruir el iterador de streaming (ver `_log_and_rebuild_response`).

    Consideración de rendimiento:
        En modo INFO el overhead es mínimo: una lectura de `request.client`,
        una llamada a `call_next` y una llamada a `log.info`. En modo DEBUG el
        overhead es mayor porque se leen y parsean los bodies, pero este modo
        nunca debe estar activo en producción.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """
        Punto de entrada del middleware. Envuelve cada petición HTTP.

        Flujo de ejecución:
            1. Captura el timestamp inicial con `perf_counter` (resolución sub-ms).
            2. Si el logger está en DEBUG, extrae el username del JWT y loguea
               cabeceras, params y body de la petición entrante.
            3. Pasa la petición al siguiente handler de la cadena (`call_next`),
               que incluye todos los middlewares posteriores y finalmente el router.
            4. Calcula la latencia en milisegundos.
            5. Loguea siempre la línea de acceso en INFO.
            6. Si está en DEBUG y la respuesta es JSON, captura y loguea el body
               de respuesta reconstruyendo el objeto `Response` (ver
               `_log_and_rebuild_response`).

        Args:
            request:   Petición HTTP entrante (Starlette `Request`).
            call_next: Callable que invoca el siguiente elemento en la cadena de
                       middlewares y finalmente el endpoint FastAPI.

        Retorna:
            La `Response` HTTP, potencialmente reconstruida si el body fue
            capturado para logging en modo DEBUG.
        """
        start = time.perf_counter()
        client_ip = request.client.host if request.client else "unknown"

        # Se evalúa el nivel del logger una sola vez por petición para evitar
        # múltiples llamadas a `isEnabledFor` durante el procesamiento.
        is_debug = log.isEnabledFor(logging.DEBUG)
        username: str | None = None

        # ── Logging detallado de REQUEST (sólo DEBUG) ──────────────────────────
        if is_debug:
            # La extracción del username se hace aquí (antes de call_next) para
            # que esté disponible tanto en el log de REQUEST como en el de acceso
            # INFO posterior. En modo INFO se omite para evitar el coste de
            # decodificar el JWT en cada petición de producción.
            username = _extract_username(request)
            safe_headers = _sanitize_headers(dict(request.headers))
            query_params = dict(request.query_params)
            path_params  = dict(request.path_params)

            log.debug(
                "→ REQUEST  %s %s | ip=%s | user=%s",
                request.method, request.url.path, client_ip, username or "anon",
            )
            log.debug("  headers     : %s", safe_headers)
            log.debug("  query_params: %s", query_params)
            log.debug("  path_params : %s", path_params)

            content_type = request.headers.get("content-type", "")
            if any(ct in content_type for ct in _LOG_BODY_CONTENT_TYPES):
                await _log_request_body(request)

        # ── Ejecutar el handler ────────────────────────────────────────────────
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000

        # ── Logging INFO (siempre) ─────────────────────────────────────────────
        log.info(
            "%s %s %d %.1fms ip=%s user=%s",
            request.method, request.url.path,
            response.status_code, elapsed_ms,
            client_ip, username or "anon",
        )

        # ── Logging detallado de RESPONSE (sólo DEBUG + JSON) ─────────────────
        if is_debug:
            resp_content_type = response.headers.get("content-type", "")
            if "application/json" in resp_content_type:
                # El body de respuesta en Starlette es un iterador asíncrono de
                # streaming. Para loguearlo es necesario consumirlo completamente,
                # lo que invalida el iterador original. Por eso `_log_and_rebuild_response`
                # devuelve una nueva `Response` con el body ya materializado.
                response = await _log_and_rebuild_response(
                    response, request, elapsed_ms, username
                )

        return response


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _log_request_body(request: Request) -> None:
    """
    Lee y loguea el body de la petición entrante a nivel DEBUG.

    Starlette cachea el resultado de `request.body()` internamente: la primera
    llamada lee del socket y lo almacena; llamadas posteriores devuelven el
    mismo buffer sin releer. Esto es lo que hace seguro llamar a esta función
    sin "consumir" el body antes de que llegue al endpoint.

    El body se intenta parsear como JSON para sanitizar campos sensibles con
    `_sanitize_dict`. Si el parseo falla (p. ej., es form-urlencoded o binario),
    se loguea solo el tamaño en bytes sin mostrar el contenido.

    Cualquier excepción durante la lectura o parseo se captura silenciosamente:
    un error en el logging no debe interrumpir el procesamiento de la petición.

    Args:
        request: Petición entrante cuyo body se desea loguear.
    """
    try:
        body_bytes = await request.body()
        if not body_bytes:
            return
        text = body_bytes.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                parsed = _sanitize_dict(parsed)
            log.debug("  request body: %s", _truncate(json.dumps(parsed, ensure_ascii=False)))
        except (json.JSONDecodeError, ValueError):
            log.debug("  request body: <non-JSON, %d bytes>", len(body_bytes))
    except Exception as exc:
        log.debug("  request body: <error leyendo: %s>", exc)


async def _log_and_rebuild_response(
    response: Response,
    request: Request,
    elapsed_ms: float,
    username: str | None,
) -> Response:
    """
    Consume el iterador de streaming de la respuesta, loguea su body y devuelve
    una nueva `Response` con el contenido ya materializado en memoria.

    Problema que resuelve:
        En Starlette, el body de una `Response` generada por FastAPI es un
        iterador asíncrono (`body_iterator`). Si se consume para loguearlo sin
        reconstruir la respuesta, el cliente recibiría una respuesta vacía. Esta
        función resuelve el problema en tres pasos:
          1. Consume todos los chunks del iterador y los concatena en `body_bytes`.
          2. Loguea el body (parseando JSON y truncando si es necesario).
          3. Construye y devuelve una nueva `Response` con `body_bytes` como
             contenido, preservando status code y cabeceras.

    Cabecera `content-length`:
        Se elimina deliberadamente de las cabeceras al reconstruir la respuesta.
        Starlette recalcula automáticamente `content-length` a partir del body
        materializado. Si se mantuviera el valor original (que corresponde al
        body sin comprimir o parcial), el cliente podría recibir un valor incorrecto
        que causaría errores de parseo en la respuesta.

    Si ocurre cualquier error durante la captura (p. ej., el iterador ya fue
    consumido por otro middleware), se loguea el error a nivel DEBUG y se devuelve
    la respuesta original intacta para no interrumpir el flujo.

    Args:
        response:   Respuesta generada por el endpoint FastAPI.
        request:    Petición original, usada para enriquecer el log con método y ruta.
        elapsed_ms: Latencia en milisegundos calculada en `dispatch`.
        username:   Nombre del usuario autenticado o `None` si es anónimo.

    Retorna:
        Nueva `Response` con el mismo status, cabeceras (sin content-length) y
        body que la respuesta original, o la respuesta original si hubo un error.
    """
    try:
        chunks: list[bytes] = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)
        body_bytes = b"".join(chunks)

        log.debug(
            "← RESPONSE %s %s | status=%d | %.1fms | size=%d | user=%s",
            request.method, request.url.path,
            response.status_code, elapsed_ms,
            len(body_bytes), username or "anon",
        )
        try:
            parsed = json.loads(body_bytes.decode("utf-8", errors="replace"))
            log.debug(
                "  response body: %s",
                _truncate(json.dumps(parsed, ensure_ascii=False)),
            )
        except Exception:
            pass

        # Reconstruir response sin content-length (Starlette lo recalcula)
        headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
        return Response(
            content=body_bytes,
            status_code=response.status_code,
            headers=headers,
        )
    except Exception as exc:
        log.debug("  response body: <error capturando: %s>", exc)
        return response
