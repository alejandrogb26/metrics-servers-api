"""
Este archivo proporciona una función para crear y gestionar el cliente de MinIO,
utilizando la configuración definida en el archivo `config.py`. El cliente MinIO
es utilizado para interactuar con el servicio de almacenamiento de objetos MinIO.

Dependencias:
    - Minio: Cliente oficial de MinIO para interactuar con el servicio de almacenamiento de objetos.
    - core.config.get_settings: Para obtener la configuración de MinIO desde el archivo de configuración.
    - urllib.parse.urlparse: Para analizar la URL de conexión de MinIO.
"""

from functools import lru_cache
from urllib.parse import urlparse

from minio import Minio

from core.config import get_settings


@lru_cache
def get_minio_client() -> Minio:
    """
    Crea y devuelve una instancia del cliente MinIO configurado con los valores 
    definidos en el archivo de configuración. Se utiliza `@lru_cache` para 
    almacenar en caché la instancia y evitar la creación repetida del cliente.

    La configuración de MinIO (endpoint, claves de acceso y secretas) se obtiene
    a través de la función `get_settings`.

    Retorna:
        Minio: Instancia del cliente MinIO configurado.

    Proceso:
        1. Obtiene la configuración de MinIO desde `get_settings()`.
        2. Analiza el `minio_endpoint` para extraer el esquema (https o http) y el host.
        3. Crea y retorna un cliente MinIO utilizando las claves de acceso y secretas obtenidas.
    """
    settings = get_settings()  # Obtener la configuración de MinIO desde el archivo de configuración
    parsed = urlparse(settings.minio_endpoint)  # Analizar la URL de endpoint de MinIO
    secure = parsed.scheme == "https"  # Determinar si la conexión debe ser segura (HTTPS)
    host = parsed.netloc or parsed.path  # Obtener el host, soporta con o sin esquema

    # Crear y retornar la instancia del cliente MinIO con la configuración
    return Minio(
        host,
        access_key=settings.minio_access_key,  # Clave de acceso de MinIO
        secret_key=settings.minio_secret_key,  # Clave secreta de MinIO
        secure=secure,  # Determinar si la conexión es segura
    )