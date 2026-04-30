from sqlalchemy import func
from sqlmodel import Session, select

from models.ambito import Ambito, AmbitoRead
from models.permiso import Permiso, PermisoRead


class PermisoRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find_by_id(self, permiso_id: int) -> PermisoRead | None:
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
