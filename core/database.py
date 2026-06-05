"""
Módulo de inicialización y gestión del motor de base de datos relacional.

Capa arquitectónica: Infraestructura / Persistencia relacional.

Responsabilidades:
    - Crear y exponer el engine SQLAlchemy (`engine`) que representa el pool de
      conexiones hacia MariaDB. Es el único punto del proyecto donde se instancia
      el engine; todos los demás módulos lo consumen a través de `get_session()`.
    - Proporcionar `get_session()`, un generador pensado para ser usado como
      dependencia FastAPI (`Depends(get_session)`), que entrega una `Session`
      abierta al endpoint y la cierra automáticamente al terminar la petición,
      incluso en caso de excepción.
    - Exponer `create_db_tables()` para la creación inicial del esquema en
      entornos donde no se usan migraciones Alembic.

Qué NO debe contener este fichero:
    - Definiciones de modelos SQLModel/SQLAlchemy. Eso pertenece a `models/`.
    - Consultas SQL o lógica de acceso a datos. Eso pertenece a `repositories/`.
    - Lógica de negocio de ningún tipo.
    - Configuración de conexiones a otras bases de datos (MongoDB, Redis).
      MongoDB tiene su propio cliente en `core/dependencies.py`.

Relaciones con otros módulos:
    - `core/config.py` → proporciona `database_url` con las credenciales y
      localización de MariaDB.
    - `core/dependencies.py` → re-exporta `get_session` como dependencia
      FastAPI para que los routers no importen directamente de este módulo.
    - `repositories/` → todos los repositorios relacionales reciben una `Session`
      inyectada por `get_session`.
    - `main.py` → llama a `create_db_tables()` en el evento de arranque (`lifespan`)
      para garantizar que el esquema existe antes de aceptar peticiones.
"""

from collections.abc import Generator
from sqlmodel import Session, SQLModel, create_engine

from core.config import get_settings

# Se obtiene la configuración en el momento de importar el módulo (no dentro de una
# función) porque el engine debe existir durante todo el ciclo de vida de la
# aplicación. `get_settings()` está cacheada con @lru_cache, así que esta llamada
# no supone una lectura adicional del fichero .env.
_settings = get_settings()

# Engine de SQLAlchemy: representa el pool de conexiones hacia MariaDB.
#
# Parámetros del pool:
#   - pool_pre_ping=True: antes de entregar una conexión del pool, SQLAlchemy
#     ejecuta un "SELECT 1" de verificación. Esto detecta conexiones que han sido
#     cerradas por el servidor MariaDB (por timeout de `wait_timeout`) y las
#     descarta, evitando errores "MySQL server has gone away" en peticiones
#     de larga duración o tras períodos de inactividad.
#
#   - pool_size=10: número de conexiones persistentes mantenidas en el pool.
#     Ajustar según la carga esperada y el `max_connections` configurado en MariaDB.
#
#   - max_overflow=20: conexiones adicionales que se pueden abrir por encima de
#     `pool_size` cuando el pool está saturado. El número máximo absoluto de
#     conexiones simultáneas es pool_size + max_overflow = 30. Pasado ese límite,
#     SQLAlchemy bloqueará hasta que se libere una conexión (o lanzará timeout).
#
#   - echo=False: deshabilita el logging de SQL generado. Activar temporalmente
#     a `True` (o usar `echo="debug"`) durante el desarrollo para inspeccionar
#     las consultas generadas por SQLModel/SQLAlchemy.
engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    echo=False,
)

def create_db_tables() -> None:
    """
    Crea en MariaDB todas las tablas definidas en los modelos SQLModel que aún
    no existan (`CREATE TABLE IF NOT EXISTS`).

    Itera sobre el `MetaData` global de SQLModel, que se puebla automáticamente
    cuando se importan los módulos de `models/` (cada clase `SQLModel` con
    `table=True` registra su tabla en ese MetaData al ser definida).

    Uso típico:
        Se invoca una sola vez durante el arranque de la aplicación, en el
        handler `lifespan` de `main.py`, después de importar todos los modelos
        para asegurar que están registrados en el MetaData.

    Consideraciones:
        - Solo crea tablas nuevas; no modifica columnas ni índices de tablas
          existentes. Para cambios de esquema en producción se debe usar
          Alembic u otra herramienta de migraciones.
        - En entornos con migraciones gestionadas (Alembic), esta función puede
          resultar redundante o incluso conflictiva si Alembic gestiona el estado
          del esquema. En ese caso se recomienda desactivarla.
        - No es thread-safe si se llama concurrentemente, aunque en la práctica
          FastAPI la invoca una sola vez en el evento de arranque (proceso único
          o worker principal en modo multi-worker).
    """
    SQLModel.metadata.create_all(engine)

def get_session() -> Generator[Session, None, None]:
    """
    Generador que proporciona una `Session` SQLAlchemy/SQLModel por petición HTTP.

    Diseñado para ser usado como dependencia FastAPI:

        from fastapi import Depends
        from core.database import get_session

        @router.get("/ruta")
        def mi_endpoint(session: Session = Depends(get_session)):
            ...

    Ciclo de vida de la sesión:
        1. Al entrar en el bloque `with Session(engine)`, SQLAlchemy obtiene una
           conexión del pool y abre una transacción implícita.
        2. `yield session` entrega el control al endpoint. Cualquier operación
           de escritura realizada en la sesión queda pendiente de commit.
        3. Al salir del bloque `with` (tanto en el flujo normal como en caso de
           excepción), SQLAlchemy hace rollback de transacciones no commiteadas y
           devuelve la conexión al pool.

    Responsabilidad del commit:
        Esta función NO hace commit. Es responsabilidad del repositorio o del
        servicio llamante invocar `session.commit()` o `session.refresh()` tras
        las operaciones de escritura. Este diseño evita commits implícitos no
        deseados y hace el control transaccional explícito.

    Yields:
        Session: Sesión activa lista para ejecutar consultas y operaciones DML.

    Efectos secundarios:
        - Consume una conexión del pool durante la duración de la petición.
          Si todas las conexiones están ocupadas y se supera `max_overflow`,
          SQLAlchemy bloqueará hasta liberar una o lanzará `TimeoutError`.
        - En caso de excepción no controlada en el endpoint, el `with` hace
          rollback automático, garantizando integridad transaccional.
    """
    with Session(engine) as session:
        yield session
