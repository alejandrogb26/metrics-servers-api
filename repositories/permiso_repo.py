"""
Repositorio de acceso a datos para la entidad Permiso.

Capa arquitectónica: Infraestructura / Persistencia relacional.

Responsabilidades:
    - Encapsular las consultas SQL sobre la tabla `permisos`, incluyendo
      el JOIN con `ambitos` necesario para construir el `PermisoRead` completo.
    - Devolver directamente `PermisoRead` (con el ámbito embebido) en lugar
      de objetos ORM desnudos, delegando la transformación al helper `_map`.

Qué NO debe contener este fichero:
    - Operaciones de escritura. Los permisos son datos de catálogo configurados
      en el arranque del sistema; no se crean ni modifican a través de la API.
    - Lógica de asignación de permisos a grupos. Eso pertenece a
      `repositories/grupo_repo.py`.

Relaciones con otros módulos:
    - `models/permiso.py`   → `Permiso` (ORM) y `PermisoRead` (esquema de respuesta).
    - `models/ambito.py`    → `Ambito` (ORM) y `AmbitoRead` (embebido en `PermisoRead`).
    - `core/database.py`    → proporciona la `Session` inyectada en el constructor.
    - Servicios y routers   → instancian `PermisoRepository(session)` para obtener
                              listados y búsquedas de permisos.

Autor:
    Alejandro Gómez Blanco

Proyecto:
    Metrics Servers

Versión:
    1.0.0

Organización:
    Metrics Servers Project
"""

from sqlalchemy import func
from sqlmodel import Session, select

from models.ambito import Ambito, AmbitoRead
from models.permiso import Permiso, PermisoRead


class PermisoRepository:
    """
    Repositorio de solo lectura para la tabla `permisos`.

    Todas las queries incluyen un JOIN con `ambitos` porque `PermisoRead`
    requiere el ámbito completo embebido. No es posible usar `session.get()`
    (optimizado para PK de un único modelo) ya que cada permiso necesita
    datos de dos tablas simultáneamente.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def find_by_id(self, permiso_id: int) -> PermisoRead | None:
        """
        Busca un permiso por PK con su ámbito cargado en una sola query.

        Usa `select(Permiso, Ambito)` con JOIN explícito en lugar de
        `session.get(Permiso, permiso_id)` seguido de una carga separada del
        ámbito. Esto evita una segunda query a la BD y garantiza que `PermisoRead`
        siempre llega construido con ambos objetos.

        Cuando SQLModel selecciona múltiples modelos, el resultado de cada fila
        es una tupla; `permiso, ambito = result` la desempaqueta antes de pasar
        ambos objetos al helper `_map`.

        Args:
            permiso_id: Clave primaria del permiso a buscar.

        Retorna:
            `PermisoRead` con ámbito embebido, o `None` si no existe.
        """
        stmt = (
            select(Permiso, Ambito)
            .join(Ambito, Ambito.id == Permiso.ambito_id)
            .where(Permiso.id == permiso_id)
        )
        result = self.session.exec(stmt).first()  # type: ignore[call-overload]
        if result is None:
            return None
        permiso, ambito = result
        return self._map(permiso, ambito)

    def find_all(self, offset: int, limit: int) -> tuple[list[PermisoRead], int]:
        """
        Devuelve una página de permisos con sus ámbitos y el total de registros.

        A diferencia de `AmbitoRepository.find_all`, esta query incluye
        `ORDER BY ambito.nombre, permiso.nombre`. El orden doble agrupa los
        permisos por ámbito y los ordena alfabéticamente dentro de cada uno,
        produciendo una paginación determinista y una salida lógicamente
        organizada (todos los permisos de un ámbito aparecen juntos).

        Condición de carrera:
            El `COUNT(*)` y el `SELECT ... LIMIT/OFFSET` son queries separadas.
            Un cambio concurrente entre ambas puede producir una inconsistencia
            de ±1 en `total`. Aceptable dado que los permisos son datos de
            catálogo con escritura prácticamente nula en producción.

        Args:
            offset: Registros a saltar (= page * size).
            limit:  Máximo de registros a devolver (= size).

        Retorna:
            Tupla `(lista_de_PermisoRead, total_sin_paginar)`.
        """
        # COUNT(*) y SELECT LIMIT/OFFSET son dos queries separadas: un INSERT/DELETE
        # concurrente entre ellas puede hacer que `total` no coincida exactamente con
        # len(items). Trade-off aceptable para paginación de lectura sin escritura muy alta.
        total: int = self.session.exec(select(func.count()).select_from(Permiso)).one()
        stmt = (
            select(Permiso, Ambito)
            .join(Ambito, Ambito.id == Permiso.ambito_id)
            .order_by(Ambito.nombre, Permiso.nombre)
            .offset(offset)
            .limit(limit)
        )
        rows = self.session.exec(stmt).all()  # type: ignore[call-overload]
        return [self._map(p, a) for p, a in rows], total

    @staticmethod
    def _map(permiso: Permiso, ambito: Ambito) -> PermisoRead:
        """
        Convierte un par `(Permiso, Ambito)` ORM en un `PermisoRead` serializable.

        Método estático porque no necesita el estado de la sesión ni del
        repositorio: opera exclusivamente sobre objetos ORM ya cargados.

        Construye el `AmbitoRead` embebido directamente desde el objeto `Ambito`,
        evitando una query adicional. Este helper centraliza la construcción del
        DTO para que tanto `find_by_id` como `find_all` produzcan exactamente
        el mismo formato de salida.

        Args:
            permiso: Objeto ORM `Permiso` con los datos del permiso.
            ambito:  Objeto ORM `Ambito` correspondiente al `permiso.ambito_id`.

        Retorna:
            `PermisoRead` con todos los campos del permiso y el ámbito embebido.
        """
        return PermisoRead(
            id=permiso.id,  # type: ignore[arg-type]
            nombre=permiso.nombre,
            descripcion=permiso.descripcion,
            ambito=AmbitoRead(
                id=ambito.id,  # type: ignore[arg-type]
                nombre=ambito.nombre,
                descripcion=ambito.descripcion,
            ),
        )
