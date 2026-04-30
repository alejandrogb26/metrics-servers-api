"""
Este archivo contiene la configuración y las funciones necesarias para interactuar
con la base de datos utilizando **SQLAlchemy** y **SQLModel**. 
Establece la conexión con la base de datos y proporciona las herramientas necesarias
para crear las tablas y gestionar las sesiones de la base de datos.

Dependencias:
    - SQLModel: Para la interacción con la base de datos utilizando SQLAlchemy.
    - core.config.get_settings: Para obtener la configuración de la base de datos.
"""

from collections.abc import Generator
from sqlmodel import Session, SQLModel, create_engine

from core.config import get_settings

# Obtener la configuración desde el archivo de configuración
_settings = get_settings()

# Crear el motor de la base de datos utilizando la URL de conexión obtenida de la configuración
engine = create_engine(
    _settings.database_url,  # URL de la base de datos (MariaDB)
    pool_pre_ping=True,  # Habilita el ping antes de obtener una conexión del pool (para verificar que esté viva)
    pool_size=10,  # Número de conexiones en el pool
    max_overflow=20,  # Número máximo de conexiones adicionales permitidas sobre el tamaño del pool
    echo=False,  # No muestra el SQL generado por SQLAlchemy
)

def create_db_tables() -> None:
    """
    Crea las tablas en la base de datos que aún no existan. 

    Esta función es útil en entornos de desarrollo donde se necesitan crear tablas
    de manera automática según los modelos definidos en la aplicación.

    Utiliza `SQLModel.metadata.create_all(engine)` para crear todas las tablas
    necesarias en la base de datos.
    """
    SQLModel.metadata.create_all(engine)

def get_session() -> Generator[Session, None, None]:
    """
    Proporciona una sesión de base de datos para cada solicitud en FastAPI.

    Esta función es utilizada como una dependencia en FastAPI para abrir una
    sesión de base de datos antes de procesar una solicitud y cerrarla cuando
    la solicitud haya terminado.

    Retorna:
        Generator[Session, None, None]: Un generador que abre y cierra una sesión 
        de base de datos al utilizarse como una dependencia.
    """
    with Session(engine) as session:
        yield session