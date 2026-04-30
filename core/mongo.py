"""
Este archivo proporciona funciones para crear y gestionar la conexión con MongoDB
utilizando la configuración definida en el archivo `config.py`. Las funciones
permiten obtener una instancia de cliente MongoDB y acceder a la base de datos
especificada en la configuración.

Dependencias:
    - pymongo.MongoClient: Cliente oficial de MongoDB para interactuar con la base de datos.
    - core.config.get_settings: Para obtener la configuración de MongoDB desde el archivo de configuración.
"""

from functools import lru_cache
from pymongo import MongoClient
from pymongo.database import Database

from core.config import get_settings


@lru_cache
def get_mongo_client() -> MongoClient:
    """
    Crea y devuelve una instancia del cliente MongoDB configurado con los valores 
    definidos en el archivo de configuración. Se utiliza `@lru_cache` para 
    almacenar en caché la instancia y evitar la creación repetida del cliente.

    La configuración de MongoDB (URI de conexión) se obtiene a través de la función `get_settings`.

    Retorna:
        MongoClient: Instancia del cliente MongoDB configurado.
    """
    settings = get_settings()  # Obtener la configuración de MongoDB desde el archivo de configuración
    return MongoClient(settings.mongo_uri)  # Crear y retornar la instancia del cliente MongoDB con la URI de conexión


def get_mongo_db() -> Database:
    """
    Devuelve la base de datos de MongoDB configurada en el archivo de configuración.

    Utiliza la instancia de cliente MongoDB para obtener la base de datos especificada en la configuración.

    Retorna:
        Database: La base de datos de MongoDB configurada.
    """
    settings = get_settings()  # Obtener la configuración de MongoDB
    return get_mongo_client()[settings.mongo_db]  # Obtener la base de datos configurada en `mongo_db`