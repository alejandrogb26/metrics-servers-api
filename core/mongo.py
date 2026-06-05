"""
Módulo de inicialización del cliente MongoDB.

Capa arquitectónica: Infraestructura / Persistencia no relacional.

Responsabilidades:
    - Construir y exponer una única instancia cacheada de `MongoClient` mediante
      `get_mongo_client()`, reutilizable por todos los módulos que necesiten
      conectarse al servidor MongoDB.
    - Proporcionar `get_mongo_db()` como punto de acceso directo a la base de
      datos configurada, evitando que los repositorios tengan que conocer el
      nombre de la base de datos ni gestionar el ciclo de vida del cliente.

Qué NO debe contener este fichero:
    - Consultas, operaciones sobre colecciones ni lógica de acceso a datos.
      Eso pertenece a `repositories/mongo_repo.py`.
    - Lógica de negocio de ningún tipo.
    - Definición de esquemas o modelos de documentos MongoDB.

Relaciones con otros módulos:
    - `core/config.py`             → proporciona `mongo_uri` (URI de conexión)
                                     y `mongo_db` (nombre de la base de datos).
    - `repositories/mongo_repo.py` → consume `get_mongo_db()` para realizar
                                     todas las operaciones de lectura/escritura
                                     sobre colecciones MongoDB (métricas, logs
                                     de monitorización de servidores, etc.).

Patrón de diseño:
    `MongoClient` es costoso de instanciar: al crearse realiza el descubrimiento
    del servidor (SDAM — Server Discovery and Monitoring), negocia el protocolo
    y establece el pool de conexiones. Por eso se cachea con `@lru_cache`.

    `get_mongo_db()`, en cambio, no se cachea: acceder a una base de datos desde
    un `MongoClient` ya existente (`client[nombre]`) es una operación puramente
    local y sin coste de red, por lo que el caché añadiría complejidad sin
    beneficio real.
"""

from functools import lru_cache
from pymongo import MongoClient
from pymongo.database import Database

from core.config import get_settings


@lru_cache
def get_mongo_client() -> MongoClient:
    """
    Construye y devuelve la instancia única de `MongoClient`.

    El decorador `@lru_cache` garantiza que `MongoClient` se instancia exactamente
    una vez durante el ciclo de vida del proceso. `MongoClient` es thread-safe y
    está diseñado explícitamente para ser compartido entre hilos y peticiones
    concurrentes, por lo que un único cliente para toda la aplicación es el
    patrón recomendado por la documentación oficial de pymongo.

    Comportamiento de la conexión:
        La instanciación de `MongoClient` es lazy en cuanto a la conexión TCP:
        al construirse, inicia el proceso de descubrimiento del servidor en un
        hilo de fondo (SDAM), pero no bloquea ni lanza excepción si MongoDB no
        está disponible en ese instante. El error de conectividad solo se
        manifiesta en la primera operación real contra la base de datos.

        Por defecto, pymongo espera hasta 30 segundos para seleccionar un servidor
        disponible (`serverSelectionTimeoutMS=30000`). Si MongoDB no responde en
        ese plazo durante una operación, lanza `ServerSelectionTimeoutError`.

    Retorna:
        MongoClient: Instancia cacheada del cliente MongoDB configurado con la
                     URI definida en `settings.mongo_uri`.
    """
    settings = get_settings()
    return MongoClient(settings.mongo_uri)


def get_mongo_db() -> Database:
    """
    Devuelve el handle de la base de datos MongoDB configurada.

    Obtiene el `MongoClient` cacheado y accede a la base de datos cuyo nombre
    está definido en `settings.mongo_db`. Acceder a `client[nombre_bd]` es una
    operación local sin actividad de red: pymongo devuelve un objeto `Database`
    que referencia al cliente existente, sin abrir nuevas conexiones.

    Esta función se llama desde `repositories/mongo_repo.py` en cada operación,
    pero el coste real es mínimo: `get_mongo_client()` devuelve la instancia
    cacheada en O(1) y `get_settings()` hace lo mismo al estar también cacheada.

    Retorna:
        Database: Handle de la base de datos MongoDB. No representa una conexión
                  abierta sino una referencia lógica al namespace de la BD dentro
                  del cliente compartido.
    """
    settings = get_settings()
    return get_mongo_client()[settings.mongo_db]
