from sqlalchemy import func
from sqlmodel import Session, select
from models.seccion import Seccion, SeccionCreate, SeccionPatch


class SeccionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find_by_id(self, seccion_id: int) -> Seccion | None:
        return self.session.get(Seccion, seccion_id)

    def find_all(self, offset: int, limit: int) -> tuple[list[Seccion], int]:
        # COUNT(*) y SELECT LIMIT/OFFSET son dos queries separadas: un INSERT/DELETE
        # concurrente entre ellas puede hacer que `total` no coincida exactamente con
        # len(items). Trade-off aceptable para paginación de lectura sin escritura muy alta.
        total: int = self.session.exec(select(func.count()).select_from(Seccion)).one()
        items = list(self.session.exec(select(Seccion).offset(offset).limit(limit)).all())
        return items, total

    def insert(self, data: SeccionCreate) -> Seccion:
        try:
            seccion = Seccion(nombre=data.nombre, descripcion=data.descripcion)
            self.session.add(seccion)
            self.session.commit()
            self.session.refresh(seccion)
            return seccion
        except Exception:
            self.session.rollback()
            raise

    def update(self, seccion_id: int, patch: SeccionPatch) -> bool:
        seccion = self.session.get(Seccion, seccion_id)
        if seccion is None:
            return False
        try:
            data = patch.model_dump(exclude_none=True)
            for key, value in data.items():
                setattr(seccion, key, value)
            self.session.add(seccion)
            self.session.commit()
            return True
        except Exception:
            self.session.rollback()
            raise

    def delete(self, seccion_id: int) -> bool:
        seccion = self.session.get(Seccion, seccion_id)
        if seccion is None:
            return False
        try:
            self.session.delete(seccion)
            self.session.commit()
            return True
        except Exception:
            self.session.rollback()
            raise
