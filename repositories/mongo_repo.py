from datetime import datetime, timedelta, timezone
from pymongo import DESCENDING
from pymongo.database import Database


class MongoRepository:
    COLLECTION = "host_metrics"
    # Techo absoluto de documentos por consulta: evita respuestas masivas si el agente
    # envía métricas con alta frecuencia (p.ej. 1 doc/s × 24 h = 86.400 docs sin límite).
    # La query recupera los MAX_DOCUMENTS más recientes dentro de la ventana de tiempo
    # y los devuelve en orden cronológico ascendente para facilitar el renderizado de gráficas.
    MAX_DOCUMENTS = 10_000

    def __init__(self, db: Database) -> None:
        self.db = db

    def get_metrics(self, server_id: str, minutes_back: int = 60) -> list[dict]:
        since_dt = datetime.now(tz=timezone.utc) - timedelta(minutes=minutes_back)

        # Orden descendente + limit → los MAX_DOCUMENTS más recientes dentro de la ventana.
        # Se invierten en Python para devolver orden ascendente (cronológico) al cliente.
        docs = list(
            self.db[self.COLLECTION]
            .find({"server_id": server_id, "ts": {"$gt": since_dt}}, {"_id": 0})
            .sort("ts", DESCENDING)
            .limit(self.MAX_DOCUMENTS)
        )
        docs.reverse()
        return docs

    def update_server_id(self, old_id: str, new_id: str) -> int:
        result = self.db[self.COLLECTION].update_many(
            {"server_id": old_id},
            {"$set": {"server_id": new_id}},
        )
        return result.modified_count

    def delete_by_server_id(self, server_id: str) -> int:
        result = self.db[self.COLLECTION].delete_many({"server_id": server_id})
        return result.deleted_count
