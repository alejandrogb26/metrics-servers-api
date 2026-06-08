"""
Middleware que inyecta cabeceras HTTP de identificación del proyecto en todas las
respuestas de la API.

Capa arquitectónica: Infraestructura / Observabilidad.

Responsabilidades:
    - Añadir a cada respuesta HTTP un conjunto fijo de cabeceras `X-App-*` que
      permiten a cualquier consumidor (cliente, proxy, herramienta de monitorización)
      identificar el nombre, versión y autor de la API sin necesidad de consultar
      un endpoint separado.
    - Obtener los valores de las cabeceras exclusivamente desde `core/project_info.py`
      para garantizar consistencia con el resto de metadatos del proyecto.

Cabeceras añadidas:
    X-App-Name        — Nombre del proyecto (PROJECT_NAME).
    X-App-Version     — Versión semántica (PROJECT_VERSION).
    X-App-Author      — Nombre del autor (PROJECT_AUTHOR).
    X-App-Description — Descripción corta ASCII del proyecto (PROJECT_DESCRIPTION_SHORT).

Qué NO debe contener este fichero:
    - Lógica de negocio ni validaciones de dominio.
    - Datos sensibles (emails, tokens, contraseñas, rutas internas).

Compatibilidad CORS:
    Para que los clientes browser puedan leer estas cabeceras mediante JavaScript
    (p. ej., `response.headers.get("X-App-Version")`), deben estar listadas en
    `Access-Control-Expose-Headers`. Esto se configura en el `CORSMiddleware` de
    `main.py` mediante el parámetro `expose_headers`. Este middleware no gestiona
    CORS directamente: solo añade las cabeceras al objeto de respuesta.

Orden de registro en main.py:
    Este middleware debe añadirse ANTES de `CORSMiddleware` en el código de `main.py`
    (lo que lo sitúa entre DebugLoggingMiddleware y CORSMiddleware en la pila de
    Starlette). De este modo, cuando CORSMiddleware procesa la respuesta saliente,
    las cabeceras X-App-* ya están presentes y se incluyen en `expose_headers`.

Autor:
    Alejandro Gómez Blanco

Proyecto:
    Metrics Servers

Versión:
    1.0.0

Organización:
    Metrics Servers Project
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from core.project_info import (
    PROJECT_AUTHOR,
    PROJECT_DESCRIPTION_SHORT,
    PROJECT_NAME,
    PROJECT_VERSION,
)

# Cabeceras X-App-* que se añaden a todas las respuestas.
# Se construyen una sola vez a nivel de módulo para no recalcularlas en cada
# request: los valores son constantes durante el ciclo de vida del proceso.
_APP_HEADERS: dict[str, str] = {
    "X-App-Name": PROJECT_NAME,
    "X-App-Version": PROJECT_VERSION,
    "X-App-Author": PROJECT_AUTHOR,
    "X-App-Description": PROJECT_DESCRIPTION_SHORT,
}


class AppMetadataMiddleware(BaseHTTPMiddleware):
    """
    Middleware Starlette que añade cabeceras de identificación del proyecto a
    todas las respuestas HTTP de la API.

    Se basa en `BaseHTTPMiddleware` de Starlette, que envuelve cada request/response
    con un método `dispatch` asíncrono. El impacto en latencia es mínimo: solo se
    realizan asignaciones de cadenas sobre el objeto `MutableHeaders`.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        response: Response = await call_next(request)
        for header, value in _APP_HEADERS.items():
            response.headers[header] = value
        return response
