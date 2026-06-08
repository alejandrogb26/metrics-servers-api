"""
Módulo de mapeo global de excepciones a respuestas HTTP.

Capa arquitectónica: Presentación / Manejo de errores HTTP.

Responsabilidades:
    - Registrar en la instancia de FastAPI todos los manejadores de excepción
      globales mediante `register_exception_handlers`, llamada una sola vez
      durante el arranque de la aplicación desde `main.py`.
    - Traducir cada tipo de excepción de dominio o de infraestructura a la
      respuesta HTTP semánticamente correcta: código de estado, cuerpo JSON
      normalizado y nivel de log apropiado.
    - Garantizar que ningún detalle interno de la implementación (trazas de
      pila, mensajes de error de SQLAlchemy, nombres de tablas, etc.) se
      filtre al cliente en situaciones de error no controlado.

Qué NO debe contener este fichero:
    - Lógica de negocio ni de dominio.
    - Lanzamiento de excepciones. Los handlers solo capturan y transforman.
    - Handlers específicos de un endpoint concreto. Esos se implementan con
      bloque try/except dentro del propio router.

Relaciones con otros módulos:
    - `main.py`                 → llama a `register_exception_handlers(app)`
                                  durante la fase de arranque, antes de montar
                                  los routers.
    - `exceptions/errors.py`   → define las excepciones de dominio que este
                                  módulo captura y traduce.
    - Toda la capa de servicios → lanza las excepciones de dominio que este
                                  módulo intercepta.

Equivalencias con el sistema Java anterior:
    El conjunto de handlers registrado aquí es el equivalente funcional de los
    `ExceptionMapper<T>` de JAX-RS (`*Mapper.java`). En JAX-RS cada mapper se
    registraba como una clase independiente; aquí se agrupan como funciones
    internas dentro de `register_exception_handlers` para mantener cohesión y
    simplicidad, aprovechando que Python permite decorar funciones anidadas.

Contrato de respuesta HTTP:
    Todos los handlers devuelven JSON con la estructura:
        {"error": "CODIGO_MAYUSCULAS", "message": "Descripción legible"}
    `pydantic_validation_handler` añade además:
        {"error": "...", "message": "...", "details": [...errores_pydantic...]}
    Esta estructura es el contrato que consumen los clientes Flutter y Swing.
    Cambiarla requeriría actualizar ambos clientes.

Tabla de mapeo excepción → HTTP:
    NotFoundException         → 404  NOT_FOUND                (log: DEBUG)
    ValidationException       → 422  VALIDATION_ERROR         (log: DEBUG)
    RequestValidationError    → 422  VALIDATION_ERROR+details (log: DEBUG)
    DaoException              → 500  DAO_ERROR                (log: ERROR)
    ProbeException            → 502  PROBE_ERROR              (log: WARNING)
    IntegrityError            → 409  CONFLICT                 (log: WARNING)
    ValueError                → 400  BAD_REQUEST              (log: DEBUG)
    Exception (catch-all)     → 500  INTERNAL_ERROR           (log: ERROR/EXCEPTION)

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

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import IntegrityError

from exceptions.errors import (
    DaoException,
    NotFoundException,
    ProbeException,
    ValidationException,
)

log = logging.getLogger("api.exceptions")


def register_exception_handlers(app: FastAPI) -> None:
    """
    Registra todos los manejadores de excepción globales en la aplicación FastAPI.

    Equivalente al conjunto de `ExceptionMapper<T>` de JAX-RS.

    Patrón de implementación:
        Los handlers se definen como funciones internas (closures) y se registran
        mediante el decorador `@app.exception_handler(TipoExcepcion)`. Este patrón
        permite pasar la instancia `app` como cierre sin necesidad de variables
        globales ni de inyectar la app en cada handler individualmente. Es el
        patrón recomendado por la documentación oficial de FastAPI para registrar
        handlers en el momento del arranque.

    Orden de captura:
        FastAPI evalúa los handlers en el orden de especificidad de la excepción.
        El handler de `Exception` (catch-all) se registra al final y actúa como
        red de seguridad: captura cualquier excepción no anticipada que escape de
        los handlers anteriores o que no sea instancia de ningún tipo específico
        registrado.

    Niveles de log por handler:
        - DEBUG  → errores esperados y controlados (recurso no encontrado,
                   validación fallida, valor incorrecto). Son condiciones normales
                   de operación, no indican problemas en el sistema.
        - WARNING → condiciones anómalas recuperables (conflicto de integridad,
                    servidor remoto inaccesible). Merecen atención pero no son
                    errores internos de la API.
        - ERROR  → errores internos no esperados (fallos de base de datos,
                   excepciones no anticipadas). Requieren investigación por el
                   equipo de operaciones.

    Args:
        app: Instancia de la aplicación FastAPI sobre la que se registran los
             handlers. Debe ser la misma instancia que monta los routers.
    """

    @app.exception_handler(NotFoundException)
    async def not_found_handler(request: Request, exc: NotFoundException):
        """
        Maneja `NotFoundException` → HTTP 404 Not Found.

        Se activa cuando un servicio o repositorio no encuentra la entidad
        solicitada (servidor por ID, grupo por nombre, etc.). El mensaje de
        la excepción se devuelve al cliente directamente porque no contiene
        información sensible del sistema.

        Logging en DEBUG (no WARNING/ERROR) porque un 404 es una condición
        de operación completamente normal: el cliente puede haber solicitado
        un recurso que nunca existió o que fue eliminado.
        """
        log.debug("NOT_FOUND %s %s: %s", request.method, request.url.path, exc)
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "NOT_FOUND", "message": str(exc)},
        )

    @app.exception_handler(ValidationException)
    async def validation_handler(request: Request, exc: ValidationException):
        """
        Maneja `ValidationException` → HTTP 422 Unprocessable Entity.

        Se activa cuando los datos son estructuralmente correctos (Pydantic los
        aceptó) pero violan una regla de negocio: nombre duplicado, estado
        incompatible con la operación solicitada, etc.

        Se elige 422 sobre 400 porque el problema no es el formato de los datos
        (que es válido) sino su significado en el contexto actual del sistema.
        El mensaje de la excepción se devuelve al cliente para que pueda mostrar
        un mensaje de error descriptivo al usuario.
        """
        log.debug("VALIDATION_ERROR %s %s: %s", request.method, request.url.path, exc)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"error": "VALIDATION_ERROR", "message": str(exc)},
        )

    @app.exception_handler(RequestValidationError)
    async def pydantic_validation_handler(request: Request, exc: RequestValidationError):
        """
        Maneja `RequestValidationError` de Pydantic → HTTP 422 Unprocessable Entity.

        Se activa cuando FastAPI/Pydantic rechaza el cuerpo de la petición por
        incumplir el esquema del modelo: campo obligatorio ausente, tipo incorrecto,
        valor fuera de rango, etc. Este handler reemplaza el handler por defecto
        de FastAPI para normalizar el formato de respuesta al mismo contrato JSON
        que usan los demás handlers de esta API.

        A diferencia de `validation_handler` (que maneja errores de negocio),
        este handler incluye el campo `details` con la lista completa de errores
        de Pydantic (`exc.errors()`), lo que permite al cliente mostrar mensajes
        de validación específicos por campo (p. ej. en un formulario Flutter).

        El mensaje genérico de `message` es fijo e intencionadamente ambiguo para
        no revelar el esquema interno del modelo al cliente.
        """
        log.debug("PYDANTIC_VALIDATION %s %s: %s", request.method, request.url.path, exc.errors())
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": "VALIDATION_ERROR",
                "message": "Error de validación en los datos de entrada",
                "details": exc.errors(),
            },
        )

    @app.exception_handler(DaoException)
    async def dao_handler(request: Request, exc: DaoException):
        """
        Maneja `DaoException` → HTTP 500 Internal Server Error.

        Se activa cuando una operación de persistencia (SQL en MariaDB, operación
        en MongoDB) falla de forma inesperada. Se loguea en nivel ERROR porque
        cualquier fallo de acceso a datos es un problema interno que requiere
        atención del equipo de operaciones.

        El atributo `exc.cause` (la excepción original de SQLAlchemy o pymongo)
        NO se incluye en la respuesta al cliente: podría revelar nombres de tablas,
        columnas, topología de la base de datos u otros detalles de implementación.
        Solo el mensaje de la `DaoException` (genérico, definido por el repositorio)
        se devuelve al cliente.
        """
        log.error("DAO_ERROR %s %s: %s", request.method, request.url.path, exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "DAO_ERROR", "message": str(exc)},
        )

    @app.exception_handler(ProbeException)
    async def probe_handler(request: Request, exc: ProbeException):
        """
        Maneja `ProbeException` → HTTP 502 Bad Gateway.

        Se activa cuando el sondeo SSH de un servidor remoto falla: host
        inaccesible, timeout de conexión, autenticación rechazada, etc.

        Se elige 502 (Bad Gateway) sobre 500 (Internal Server Error) de forma
        deliberada: el fallo no es interno a la API sino que la API actuó como
        intermediario y el sistema externo (el servidor sondeado) no respondió
        correctamente. Este código HTTP informa al cliente de que el problema
        está en el servidor remoto, no en la API.

        Se loguea en WARNING (no ERROR) porque un servidor remoto inaccesible
        es una condición operativa esperada (el servidor puede estar apagado,
        en mantenimiento, o con red cortada), no un fallo interno de la API.
        """
        log.warning("PROBE_ERROR %s %s: %s", request.method, request.url.path, exc)
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"error": "PROBE_ERROR", "message": str(exc)},
        )

    @app.exception_handler(IntegrityError)
    async def integrity_handler(request: Request, exc: IntegrityError):
        """
        Maneja `IntegrityError` de SQLAlchemy → HTTP 409 Conflict.

        Se activa cuando una operación SQL viola una restricción de integridad
        de la base de datos: clave primaria duplicada, violación de UNIQUE,
        referencia a una clave foránea inexistente, etc.

        Este handler captura `IntegrityError` directamente desde SQLAlchemy,
        no envuelto en `DaoException`, porque el patrón de conflicto de
        integridad tiene un código HTTP semántico propio (409) distinto del
        500 genérico que devolvería `DaoException`.

        Seguridad: `exc.orig` (el error original del driver MySQL/MariaDB) se
        loguea para diagnóstico pero NO se incluye en la respuesta al cliente.
        Ese error puede contener nombres de columnas, valores duplicados u
        otros detalles del esquema que no deben exponerse. El mensaje al cliente
        es siempre el genérico fijo de este handler.
        """
        log.warning("INTEGRITY_ERROR %s %s: %s", request.method, request.url.path, exc.orig)
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": "CONFLICT", "message": "Conflicto de integridad en la base de datos"},
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
        """
        Maneja `ValueError` de Python → HTTP 400 Bad Request.

        Actúa como red de seguridad para los `ValueError` que los servicios o
        utilidades lanzan ante datos de entrada incorrectos que no se modelaron
        como `ValidationException`. Devuelve el mensaje de la excepción al
        cliente porque `ValueError` suele usarse con mensajes descriptivos del
        problema.

        Logging en DEBUG porque un `ValueError` ante entrada incorrecta es una
        condición esperable durante el uso normal de la API.
        """
        log.debug("VALUE_ERROR %s %s: %s", request.method, request.url.path, exc)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "BAD_REQUEST", "message": str(exc)},
        )

    @app.exception_handler(Exception)
    async def generic_handler(request: Request, exc: Exception):
        """
        Manejador catch-all para cualquier excepción no anticipada → HTTP 500.

        Red de seguridad final: captura toda excepción que no haya sido
        interceptada por un handler más específico. Garantiza que la API nunca
        devuelve una respuesta no estructurada (traceback en texto plano) ante
        un error inesperado.

        Estrategia de logging según el nivel activo:
            - Modo DEBUG (`log.isEnabledFor(logging.DEBUG)` es True):
              usa `log.exception`, que emite el mensaje junto con el traceback
              completo. Imprescindible durante el desarrollo para diagnosticar
              la causa raíz.
            - Modo INFO (producción): usa `log.error` con solo el tipo de
              excepción, sin traceback. Reduce el volumen de logs y evita que
              trazas de pila largas inunden sistemas de logging centralizados,
              a costa de requerir correlación manual con el código para el
              diagnóstico.

        Seguridad: el cuerpo de la respuesta es siempre el mensaje genérico
        "Error interno del servidor". El tipo de excepción, el mensaje y la
        traza de pila nunca se devuelven al cliente.
        """
        # En modo DEBUG se emite stacktrace completo; en INFO sólo el tipo de excepción.
        if log.isEnabledFor(logging.DEBUG):
            log.exception("UNHANDLED_ERROR %s %s", request.method, request.url.path)
        else:
            log.error("INTERNAL_ERROR %s %s [%s]",
                      request.method, request.url.path, type(exc).__name__)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "INTERNAL_ERROR", "message": "Error interno del servidor"},
        )
