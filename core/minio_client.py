"""
Módulo de inicialización del cliente MinIO.

Capa arquitectónica: Infraestructura / Almacenamiento de objetos.

Responsabilidades:
    - Construir y exponer una única instancia cacheada del cliente oficial de
      MinIO, configurada a partir de los parámetros definidos en `core/config.py`.
    - Normalizar el formato del endpoint de MinIO, que puede recibirse con o sin
      esquema URL (`https://host:puerto` o simplemente `host:puerto`), extrayendo
      el host y determinando si la conexión debe usar TLS.

Qué NO debe contener este fichero:
    - Operaciones sobre buckets ni objetos (subida, descarga, listado). Eso
      pertenece a `services/minio_service.py`.
    - Lógica de negocio de ningún tipo.
    - Creación o verificación de buckets al arrancar. Eso pertenece a `main.py`
      o a `services/minio_service.py`.

Relaciones con otros módulos:
    - `core/config.py`           → proporciona `minio_endpoint`, `minio_access_key`
                                   y `minio_secret_key`.
    - `services/minio_service.py`→ consume `get_minio_client()` para todas las
                                   operaciones con el almacén de objetos (avatares
                                   de usuario, capturas de servidores, iconos de
                                   servicios).

Patrón de diseño:
    Igual que `core/database.py` con el engine de SQLAlchemy, este módulo sigue
    el patrón de fábrica cacheada con `@lru_cache`: el cliente se construye una
    sola vez por proceso y se reutiliza en todas las llamadas posteriores. El
    cliente de MinIO gestiona internamente su propio pool de conexiones HTTP, por
    lo que crear múltiples instancias sería innecesariamente costoso.
"""

from functools import lru_cache
from urllib.parse import urlparse

from minio import Minio

from core.config import get_settings


@lru_cache
def get_minio_client() -> Minio:
    """
    Construye y devuelve la instancia única del cliente MinIO.

    El decorador `@lru_cache` garantiza que esta función se ejecuta exactamente
    una vez durante el ciclo de vida del proceso. Las llamadas posteriores
    devuelven la misma instancia sin reconstruirla. El cliente de MinIO es
    thread-safe, por lo que compartir la instancia entre peticiones concurrentes
    es seguro.

    Normalización del endpoint:
        El campo `minio_endpoint` admite dos formatos en el fichero `.env`:

        a) Con esquema explícito:  `https://minio.ejemplo.com:9000`
           → `urlparse` extrae `netloc = "minio.ejemplo.com:9000"` y
             `scheme = "https"`, por lo que `secure = True`.

        b) Sin esquema (bare host): `minio.ejemplo.com:9000` o `localhost:9000`
           → `urlparse` no reconoce `netloc` y coloca todo en `path`.
             El fallback `parsed.netloc or parsed.path` devuelve el path como host.
             Como `parsed.scheme` es `""`, `secure = False` (sin TLS).
             Este es el caso habitual en desarrollo con MinIO local.

        El constructor de `Minio` espera solo el host (con puerto opcional), sin
        el esquema. Por eso se extrae el host del resultado del parseo en lugar
        de pasar el endpoint directamente.

    Seguridad:
        En producción, `minio_endpoint` debe usar el esquema `https://` para
        cifrar la transferencia de objetos (avatares, capturas, iconos). Sin TLS,
        las credenciales de acceso (`minio_access_key`, `minio_secret_key`) viajan
        en texto claro en las cabeceras HTTP de cada operación.

    Retorna:
        Minio: Instancia del cliente MinIO lista para operar. Todas las llamadas
               posteriores a `get_minio_client()` devuelven esta misma instancia.
    """
    settings = get_settings()
    parsed = urlparse(settings.minio_endpoint)

    # Si el endpoint se declaró sin esquema (p. ej. "localhost:9000"), urlparse
    # no puede determinar netloc y lo ubica en path. El operador `or` cubre ambos
    # casos: primero intenta netloc (endpoint con esquema), luego path (sin esquema).
    secure = parsed.scheme == "https"
    host = parsed.netloc or parsed.path

    return Minio(
        host,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=secure,
    )
