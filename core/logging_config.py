"""
Módulo de configuración del sistema de logging de la aplicación.

Capa arquitectónica: Infraestructura / Observabilidad.

Responsabilidades:
    - Configurar el logger raíz de Python (`logging.root`) con el handler,
      formato y nivel adecuados al entorno (producción vs. desarrollo/debug).
    - Silenciar librerías de terceros excesivamente verbosas que, de no
      controlarse, contaminarían los logs de la aplicación con mensajes de
      bajo valor operativo.
    - Evitar la duplicación de líneas de acceso HTTP suprimiendo el logger
      de `uvicorn.access` en modo INFO (el middleware `DebugLoggingMiddleware`
      ya cubre ese rol).

Qué NO debe contener este fichero:
    - Lógica de negocio ni configuración de servicios externos.
    - Definición de handlers hacia ficheros, sistemas remotos (Loki, Splunk,
      etc.) u otros destinos. Si se necesitan, deben añadirse aquí como
      extensión de `setup_logging`, no dispersarse por otros módulos.
    - Configuración de loggers individuales de la aplicación (eso lo hace
      cada módulo con `logging.getLogger(__name__)`).

Relaciones con otros módulos:
    - `main.py` → llama a `setup_logging(debug=settings.app_debug)` una sola
      vez durante el arranque, antes de que uvicorn empiece a servir peticiones.
    - `core/debug_middleware.py` → usa el logger `api.http`, cuyo nivel efectivo
      queda determinado por la configuración del root logger establecida aquí.
    - `core/dependencies.py` → usa el logger `api.auth`, igualmente heredero
      del root logger.
    - Todos los demás módulos con `logging.getLogger(name)` heredan el nivel y
      handler del root logger configurado por esta función.

Convención de salida:
    Los logs se escriben en `sys.stdout` (no en `sys.stderr`). Esto sigue la
    convención estándar para aplicaciones contenerizadas (Docker, Kubernetes),
    donde el agente de recolección de logs (Fluentd, Promtail, etc.) lee el
    stdout del contenedor. Redirigir a stderr mezclaría logs de aplicación con
    mensajes de error del intérprete Python.

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
import sys

# Formato detallado para modo DEBUG: incluye nombre del logger y número de línea,
# lo que facilita localizar exactamente qué módulo emitió cada mensaje durante
# el desarrollo y diagnóstico.
_FMT_DEBUG = "%(asctime)s [%(levelname)-8s] %(name)s:%(lineno)d — %(message)s"

# Formato compacto para modo INFO (producción): omite nombre del logger y línea
# para reducir el tamaño de las líneas de log y facilitar su ingestión en
# sistemas centralizados. El mensaje debe ser autoexplicativo sin ese contexto.
_FMT_INFO  = "%(asctime)s [%(levelname)-8s] %(message)s"

# Formato de fecha ISO 8601 sin milisegundos, compatible con la mayoría de
# sistemas de log centralizados (Loki, ELK, Grafana).
_DATE_FMT  = "%Y-%m-%d %H:%M:%S"

# Librerías de terceros muy verbosas que silenciamos en modo INFO.
# Estas librerías emiten numerosos mensajes DEBUG e INFO propios que no tienen
# valor para el operador en condiciones normales:
#   - pymongo:   operaciones internas del driver MongoDB (pool, heartbeat, SDAM).
#   - minio:     cada petición HTTP al almacén de objetos.
#   - ldap3:     negociación de conexión y operaciones LDAP detalladas.
#   - paramiko:  handshake SSH paso a paso (extremadamente verboso).
#   - urllib3:   cada petición HTTP de bajo nivel (usada por minio y requests).
#   - asyncio:   eventos internos del bucle de eventos de Python.
_NOISY_LIBS = ("pymongo", "minio", "ldap3", "paramiko", "urllib3", "asyncio")

# Subconjunto de librerías que permanecen silenciadas incluso en modo DEBUG.
# En modo DEBUG se permiten más mensajes de terceros para facilitar el diagnóstico,
# pero paramiko y urllib3 siguen siendo demasiado verbosos para ser útiles:
#   - paramiko: loguea cada byte del protocolo SSH, lo que haría ilegibles los logs.
#   - urllib3:  loguea cabeceras y cuerpos de cada request HTTP de bajo nivel.
_NOISY_IN_DEBUG = ("paramiko", "urllib3")


def setup_logging(debug: bool = False) -> None:
    """
    Configura el sistema de logging global de la aplicación.

    Debe llamarse una única vez, al inicio del proceso, antes de que cualquier
    módulo emita logs. En `main.py` se invoca en el handler `lifespan` antes
    de arrancar uvicorn.

    Efectos:
        1. Establece el nivel del root logger a DEBUG (si debug=True) o INFO.
        2. Reemplaza todos los handlers existentes del root logger por un único
           `StreamHandler` hacia `sys.stdout` con el formato apropiado.
           El `root.handlers.clear()` es deliberado: uvicorn instala sus propios
           handlers al arrancar y, sin limpiarlos, cada mensaje aparecería dos
           veces en los logs.
        3. Eleva a WARNING el nivel de las librerías ruidosas para que sus
           mensajes DEBUG/INFO no contaminen la salida. El subconjunto silenciado
           varía según el modo (ver `_NOISY_LIBS` vs `_NOISY_IN_DEBUG`).
        4. En modo INFO, silencia `uvicorn.access` para evitar duplicar las
           líneas de acceso HTTP que ya genera `DebugLoggingMiddleware`. En
           modo DEBUG este logger se deja activo para disponer de la perspectiva
           de uvicorn además de la del middleware.

    Args:
        debug: Si True, activa el nivel DEBUG con formato detallado. Debe
               coincidir con el valor de `settings.app_debug` (ver `core/config.py`).
               Por defecto False (modo producción).
    """
    level = logging.DEBUG if debug else logging.INFO
    fmt   = _FMT_DEBUG if debug else _FMT_INFO

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(fmt, datefmt=_DATE_FMT))

    root = logging.getLogger()
    root.setLevel(level)
    # Limpiar handlers previos antes de añadir el nuevo. Sin este paso, uvicorn
    # (u otro framework que configure logging antes de este punto) causaría que
    # cada mensaje se emitiera múltiples veces.
    root.handlers.clear()
    root.addHandler(handler)

    # Silenciar librerías ruidosas elevando su umbral a WARNING.
    # En DEBUG se aplica un subconjunto más reducido para permitir ver mensajes
    # de pymongo, ldap3, etc., que sí pueden ser útiles al diagnosticar
    # problemas de conectividad con servicios externos.
    noisy = _NOISY_IN_DEBUG if debug else _NOISY_LIBS
    for lib in noisy:
        logging.getLogger(lib).setLevel(logging.WARNING)

    if not debug:
        # uvicorn.access ya genera una línea por request; el middleware la duplicaría
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
