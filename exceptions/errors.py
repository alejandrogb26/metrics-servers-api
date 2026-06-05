"""
Jerarquía de excepciones de dominio de la aplicación.

Capa arquitectónica: Dominio / Excepciones transversales.

Responsabilidades:
    - Definir un conjunto cerrado de excepciones propias que los repositorios,
      servicios y utilidades de la aplicación pueden lanzar para comunicar
      condiciones de error de forma semántica y tipada.
    - Desacoplar la capa de servicios y repositorios de FastAPI: ninguna de estas
      clases depende de HTTP ni de códigos de estado. La traducción a respuestas
      HTTP se realiza exclusivamente en `exceptions/handlers.py`.

Qué NO debe contener este fichero:
    - Lógica de negocio ni de manejo de errores HTTP.
    - Importaciones de FastAPI, Starlette ni de cualquier framework web.
    - Excepciones de infraestructura de terceros (SQLAlchemy, pymongo, redis).
      Las excepciones de terceros se capturan en la capa de repositorio y se
      envuelven en `DaoException` si procede.

Relaciones con otros módulos:
    - `exceptions/handlers.py`         → registra handlers de FastAPI que capturan
                                          estas excepciones y las traducen a
                                          respuestas HTTP con el código adecuado
                                          (404, 400, 500, etc.).
    - `repositories/grupo_repo.py`     → lanza `DaoException` cuando una operación
                                          SQL falla de forma inesperada.
    - `repositories/servidor_repo.py`  → lanza `DaoException` en errores de acceso
                                          a la base de datos relacional.
    - `repositories/mongo_repo.py`     → lanza `DaoException` en errores de
                                          operaciones contra MongoDB.
    - `services/servidor_service.py`   → lanza `ProbeException` cuando el sondeo
                                          SSH de un servidor falla.
    - Capa de servicios en general     → lanza `NotFoundException` y
                                          `ValidationException` para condiciones
                                          de negocio (recurso inexistente, datos
                                          inválidos).

Equivalencias con el sistema Java anterior:
    `DaoException` y `ProbeException` tienen contrapartes directas en el backend
    Java/JAX-RS original (`DaoException.java`, `ProbeException.java`). Esta
    correspondencia facilita la revisión cruzada del comportamiento entre ambos
    sistemas durante el periodo de transición.
"""


class DaoException(Exception):
    """
    Excepción de la capa de acceso a datos (DAO).

    Se lanza cuando una operación de persistencia (SQL en MariaDB o NoSQL en
    MongoDB) falla de forma inesperada: error de conexión, violación de
    restricción, timeout de consulta, etc.

    Equivalente directo a `DaoException.java` del backend Java/JAX-RS anterior.

    El atributo `cause` permite encadenar la excepción original de la librería
    de persistencia (p. ej. `sqlalchemy.exc.OperationalError` o
    `pymongo.errors.PyMongoError`) sin propagarla directamente a las capas
    superiores. Esto sirve para:
        - Mantener el detalle técnico accesible para logging y diagnóstico.
        - Evitar que excepciones internas de SQLAlchemy o pymongo escalen hasta
          el handler HTTP y queden expuestas en la respuesta al cliente.

    `exceptions/handlers.py` captura `DaoException` y la traduce a una respuesta
    HTTP 500 (Internal Server Error) con un mensaje genérico, sin exponer `cause`.

    Atributos:
        cause (Exception | None): Excepción original que motivó este error.
                                   Puede ser None si el error se generó sin una
                                   excepción subyacente (p. ej. resultado inesperado
                                   de una consulta).
    """

    def __init__(self, message: str, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.cause = cause


class ProbeException(Exception):
    """
    Excepción del subsistema de sondeo SSH de servidores.

    Se lanza cuando la comprobación de conectividad o estado de un servidor
    remoto falla mediante SSH: host inaccesible, timeout de conexión,
    autenticación denegada, error al ejecutar el comando de sondeo, etc.

    Equivalente directo a `ProbeException.java` del backend Java/JAX-RS anterior.

    Al igual que `DaoException`, incluye un atributo `cause` para preservar la
    excepción original de paramiko u otra librería SSH, manteniendo el detalle
    técnico disponible para logging sin exponerlo al cliente.

    `exceptions/handlers.py` captura `ProbeException` y la traduce a una respuesta
    HTTP adecuada (típicamente 502 Bad Gateway o 503 Service Unavailable) para
    indicar que el servidor sondado no está accesible, no que la API en sí haya
    fallado.

    Atributos:
        cause (Exception | None): Excepción original de la librería SSH o de
                                   red que causó el fallo del sondeo. Puede ser
                                   None si el error se detectó sin excepción
                                   subyacente (p. ej. respuesta inesperada del
                                   servidor).
    """

    def __init__(self, message: str, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.cause = cause


class NotFoundException(Exception):
    """
    Excepción de recurso no encontrado.

    Se lanza cuando una operación busca una entidad por identificador y no
    existe en la base de datos: un servidor por ID, un grupo por nombre, un
    usuario en LDAP, etc.

    `exceptions/handlers.py` captura `NotFoundException` y la traduce a una
    respuesta HTTP 404 (Not Found). Esta excepción es la alternativa a devolver
    `None` desde los servicios: al lanzarla, el flujo de control se interrumpe
    de forma explícita sin necesidad de que cada llamador compruebe un valor nulo.

    No tiene constructor personalizado; hereda el de `Exception` directamente,
    lo que significa que el mensaje se pasa como argumento posicional:
        raise NotFoundException(f"Servidor con id={id} no encontrado.")
    """

    pass


class ValidationException(Exception):
    """
    Excepción de validación de negocio.

    Se lanza cuando los datos de una petición superan la validación estructural
    de Pydantic pero violan una regla de negocio que no puede expresarse en el
    esquema: nombre de grupo duplicado, formato de parámetro incompatible con
    el estado actual del sistema, operación no permitida por las reglas del
    dominio, etc.

    Se diferencia de los errores de validación de Pydantic (`RequestValidationError`)
    en que éstos detectan datos con formato incorrecto (tipo erróneo, campo
    obligatorio ausente), mientras que `ValidationException` detecta datos
    correctamente formados pero semánticamente inválidos en el contexto actual.

    `exceptions/handlers.py` captura `ValidationException` y la traduce a una
    respuesta HTTP 400 (Bad Request) o 422 (Unprocessable Entity), según el
    criterio definido en el handler.

    No tiene constructor personalizado; hereda el de `Exception` directamente:
        raise ValidationException("El nombre de grupo ya existe.")
    """

    pass
