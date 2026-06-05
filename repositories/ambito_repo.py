"""
Repositorio de acceso a datos para la entidad Ámbito.

Capa arquitectónica: Infraestructura / Persistencia relacional.

Responsabilidades:
    - Encapsular todas las consultas SQL sobre la tabla `ambitos`, proporcionando
      una interfaz orientada al dominio que oculta los detalles de SQLAlchemy a
      la capa de servicios.
    - Recibir la sesión de base de datos por inyección en el constructor, sin
      gestionar su ciclo de vida (apertura, commit, rollback, cierre). Esa
      responsabilidad pertenece al llamante (servicio o router vía DI de FastAPI).

Qué NO debe contener este fichero:
    - Lógica de negocio ni validaciones de dominio. Si un ámbito no puede
      borrarse por tener permisos asociados, esa restricción pertenece al servicio.
    - Gestión del ciclo de vida de la sesión (commit, rollback, close).
    - Construcción de esquemas de respuesta HTTP (`AmbitoRead`). El repositorio
      devuelve objetos ORM (`Ambito`); la transformación a DTO la hace el servicio
      o el router.

Relaciones con otros módulos:
    - `models/ambito.py`       → `Ambito` es el modelo ORM sobre el que opera
                                  este repositorio.
    - `core/database.py`       → proporciona la `Session` de SQLAlchemy/SQLModel
                                  que se inyecta en el constructor.
    - Servicios y routers      → instancian `AmbitoRepository(session)` y llaman
                                  a sus métodos dentro del scope de una petición HTTP.
"""

from sqlalchemy import func
from sqlmodel import Session, select
from models.ambito import Ambito


class AmbitoRepository:
    """
    Repositorio para la tabla `ambitos`.

    Implementa el patrón Repository: centraliza el acceso a datos de la entidad
    `Ambito` en un único lugar, facilitando el testeo (se puede sustituir la
    sesión real por una de test) y el mantenimiento (los cambios de esquema SQL
    solo afectan a esta clase).

    La sesión se recibe por constructor (inyección de dependencias). El repositorio
    la usa pero no la posee: no hace commit, rollback ni close. El llamante decide
    cuándo confirmar o revertir los cambios.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def find_by_id(self, ambito_id: int) -> Ambito | None:
        """
        Busca un ámbito por su clave primaria.

        Usa `Session.get()` en lugar de `select().where()` porque SQLAlchemy
        optimiza la búsqueda por PK: primero consulta el identity map de la
        sesión (caché en memoria de los objetos ya cargados en la transacción
        actual) antes de emitir una query a la BD. Si el objeto ya fue cargado
        previamente en la misma sesión, no se produce ningún viaje a la BD.

        Args:
            ambito_id: Clave primaria del ámbito a buscar.

        Retorna:
            El objeto `Ambito` si existe, o `None` si no hay ningún ámbito
            con ese identificador.
        """
        return self.session.get(Ambito, ambito_id)

    def find_all(self, offset: int, limit: int) -> tuple[list[Ambito], int]:
        """
        Devuelve una página de ámbitos y el total de registros en la tabla.

        Ejecuta dos queries SQL separadas:
            1. `COUNT(*)` sobre la tabla completa → total de ámbitos.
            2. `SELECT ... LIMIT limit OFFSET offset` → página solicitada.

        El resultado se usa para construir un `PagedResponse` en la capa
        superior, que necesita tanto los elementos de la página como el total
        para calcular `total_pages` y `has_next`.

        Condición de carrera:
            Al ser dos queries independientes, un INSERT o DELETE concurrente
            ejecutado entre ambas puede producir una ligera inconsistencia:
            `total` puede diferir en ±1 respecto al número real de elementos
            en el momento de la segunda query. Para paginación de catálogos con
            baja tasa de escritura (como los ámbitos), esta inconsistencia es
            un trade-off aceptable frente a la complejidad de una solución
            transaccionalmente estricta (p. ej. `SELECT COUNT(*) FOR UPDATE`
            o ejecutar ambas queries en la misma transacción serializable).

        Args:
            offset: Número de registros a saltar (= page * size).
            limit:  Número máximo de registros a devolver (= size).

        Retorna:
            Tupla `(items, total)` donde `items` es la lista de objetos `Ambito`
            de la página solicitada y `total` es el conteo total de ámbitos en
            la tabla en el momento de la primera query.
        """
        # COUNT(*) y SELECT LIMIT/OFFSET son dos queries separadas: un INSERT/DELETE
        # concurrente entre ellas puede hacer que `total` no coincida exactamente con
        # len(items). Trade-off aceptable para paginación de lectura sin escritura muy alta.
        total: int = self.session.exec(select(func.count()).select_from(Ambito)).one()
        items = list(self.session.exec(select(Ambito).offset(offset).limit(limit)).all())
        return items, total
