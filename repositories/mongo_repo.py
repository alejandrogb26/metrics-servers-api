"""
Repositorio de acceso a datos para métricas de servidores en MongoDB.

Capa arquitectónica: Infraestructura / Persistencia no relacional.

Responsabilidades:
    - Leer métricas de sistema de un servidor dentro de una ventana de tiempo
      (`get_metrics`), aplicando un techo de documentos para evitar respuestas
      masivas.
    - Mantener la consistencia del campo `server_id` en MongoDB cuando cambia
      en MariaDB (`update_server_id`).
    - Eliminar todos los documentos de un servidor cuando este es borrado del
      sistema (`delete_by_server_id`).

Qué NO debe contener este fichero:
    - Lógica de negocio ni transformaciones de los datos de métricas.
    - Acceso a la base de datos relacional (MariaDB). La consistencia entre
      ambas bases de datos es responsabilidad del servicio que coordina las
      operaciones.
    - Gestión del cliente MongoDB ni de la base de datos. Eso pertenece a
      `core/mongo.py`.

Relaciones con otros módulos:
    - `core/mongo.py`                → proporciona el `Database` handle inyectado
                                       en el constructor mediante `get_mongo_db()`.
    - `services/servidor_service.py` → instancia `MongoRepository` y coordina
                                       sus operaciones con las de
                                       `repositories/servidor_repo.py` para
                                       mantener la consistencia entre MariaDB
                                       y MongoDB al renombrar o borrar servidores.

Consistencia entre bases de datos:
    Este sistema usa dos almacenes de datos independientes: MariaDB (datos de
    inventario de servidores) y MongoDB (métricas de sistema en serie temporal).
    La clave de relación entre ambos es `server_id`. Cuando `server_id` cambia
    o el servidor se elimina en MariaDB, el servicio debe propagar el cambio a
    MongoDB llamando a `update_server_id` o `delete_by_server_id`. No existe
    transacción distribuida entre ambas BDs: si la operación en una BD tiene
    éxito pero falla en la otra, la consistencia debe restaurarse manualmente.

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
from datetime import datetime, timedelta, timezone

from pymongo import DESCENDING
from pymongo.database import Database

log = logging.getLogger("api.mongo")


class MongoRepository:
    """
    Repositorio para la colección `host_metrics` en MongoDB.

    Opera sobre documentos de métricas de sistema (CPU, memoria, disco, red,
    etc.) que los agentes de monitorización almacenan en MongoDB con una marca
    de tiempo (`ts`) y un identificador de servidor (`server_id`).

    Recibe el handle de base de datos (`Database`) por constructor. No gestiona
    la conexión ni el cliente MongoDB; esa responsabilidad es de `core/mongo.py`.
    """

    COLLECTION = "host_metrics"

    # Techo absoluto de documentos por consulta: evita respuestas masivas si el agente
    # envía métricas con alta frecuencia (p.ej. 1 doc/s × 24 h = 86.400 docs sin límite).
    # La query recupera los MAX_DOCUMENTS más recientes dentro de la ventana de tiempo
    # y los devuelve en orden cronológico ascendente para facilitar el renderizado de gráficas.
    MAX_DOCUMENTS = 10_000

    def __init__(self, db: Database) -> None:
        self.db = db

    def get_metrics(self, server_id: str, minutes_back: int = 60) -> list[dict]:
        """
        Devuelve las métricas de un servidor dentro de una ventana de tiempo.

        Estrategia de consulta (DESCENDING + reverse):
            Para obtener los `MAX_DOCUMENTS` más recientes sin cargar toda la
            colección, la query ordena por `ts` descendente y aplica el límite.
            Esto garantiza que, si hay más documentos que `MAX_DOCUMENTS` en la
            ventana, se descartan los más antiguos (los menos relevantes para
            la gráfica en tiempo casi-real), no los más recientes.

            Los documentos se invierten en Python tras la consulta para devolver
            orden ascendente (cronológico), que es el que esperan las librerías
            de gráficas de los clientes Flutter y Swing. La inversión en Python
            es O(N) sobre los documentos ya cargados; no afecta a la query.

        Proyección `{"_id": 0}`:
            Excluye el campo interno `_id` (ObjectId de MongoDB) de los documentos
            devueltos. Los clientes no lo necesitan y no es JSON-serializable por
            defecto sin un codificador personalizado.

        Timestamps en UTC:
            `since_dt` se genera con `timezone.utc` para coincidir con el formato
            en que los agentes almacenan los documentos. Una discrepancia de zona
            horaria produciría un filtro de ventana de tiempo incorrecto.

        Args:
            server_id:    Identificador del servidor. Corresponde al campo
                          `server_id` de los documentos en MongoDB y al campo
                          `server_id` de la tabla `servidores` en MariaDB.
            minutes_back: Tamaño de la ventana de tiempo hacia atrás desde ahora,
                          en minutos. Por defecto 60 (última hora).

        Retorna:
            Lista de documentos dict en orden cronológico ascendente (del más
            antiguo al más reciente dentro de la ventana). Lista vacía si no hay
            métricas para ese servidor en el período indicado. Cada documento
            excluye el campo `_id`.
        """
        since_dt = datetime.now(tz=timezone.utc) - timedelta(minutes=minutes_back)
        log.debug("MONGO get_metrics server_id=%s minutes_back=%d since=%s",
                  server_id, minutes_back, since_dt.isoformat())

        # Orden descendente + limit → los MAX_DOCUMENTS más recientes dentro de la ventana.
        # Se invierten en Python para devolver orden ascendente (cronológico) al cliente.
        docs = list(
            self.db[self.COLLECTION]
            .find({"server_id": server_id, "ts": {"$gt": since_dt}}, {"_id": 0})
            .sort("ts", DESCENDING)
            .limit(self.MAX_DOCUMENTS)
        )
        docs.reverse()
        log.debug("MONGO get_metrics result server_id=%s docs=%d", server_id, len(docs))
        return docs

    def update_server_id(self, old_id: str, new_id: str) -> int:
        """
        Actualiza el campo `server_id` en todos los documentos que usan el ID antiguo.

        Se llama desde el servicio de servidores cuando un servidor cambia su
        `server_id` en MariaDB, para mantener la consistencia entre ambas bases
        de datos. Sin esta operación, las métricas históricas del servidor
        quedarían asociadas a un `server_id` que ya no existe en el inventario.

        Usa `update_many` con `$set` para una sola operación en la colección,
        sin iterar por documentos en Python.

        Args:
            old_id: Valor actual de `server_id` en los documentos MongoDB.
            new_id: Nuevo valor de `server_id` que se asignará a todos esos
                    documentos.

        Retorna:
            Número de documentos modificados. 0 si no existían documentos con
            `old_id` (servidor sin métricas almacenadas aún).
        """
        log.debug("MONGO update_server_id old=%s new=%s", old_id, new_id)
        result = self.db[self.COLLECTION].update_many(
            {"server_id": old_id},
            {"$set": {"server_id": new_id}},
        )
        log.debug("MONGO update_server_id modified=%d", result.modified_count)
        return result.modified_count

    def delete_by_server_id(self, server_id: str) -> int:
        """
        Elimina todos los documentos de métricas de un servidor.

        Se llama desde el servicio de servidores cuando un servidor es eliminado
        del inventario en MariaDB. Limpia los datos históricos de MongoDB para
        evitar documentos huérfanos (métricas sin servidor correspondiente en
        el inventario).

        Usa `delete_many` para eliminar todos los documentos del servidor en
        una sola operación.

        Advertencia de irreversibilidad:
            Esta operación es permanente. Los datos de métricas eliminados no
            pueden recuperarse a menos que exista una copia de seguridad de
            MongoDB. El servicio debe confirmar la eliminación del servidor en
            MariaDB antes (o coordinado con) esta operación.

        Args:
            server_id: Identificador del servidor cuyos documentos se eliminan.

        Retorna:
            Número de documentos eliminados. 0 si el servidor no tenía métricas
            almacenadas.
        """
        log.debug("MONGO delete_by_server_id server_id=%s", server_id)
        result = self.db[self.COLLECTION].delete_many({"server_id": server_id})
        log.debug("MONGO delete_by_server_id deleted=%d", result.deleted_count)
        return result.deleted_count
