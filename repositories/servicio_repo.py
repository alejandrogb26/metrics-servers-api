from sqlalchemy import func
from sqlmodel import Session, select
from models.servicio import Servicio, ServicioCreate, ServicioPatch


class ServicioRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find_by_id(self, servicio_id: int) -> Servicio | None:
        return self.session.get(Servicio, servicio_id)

    def find_all(self, offset: int, limit: int) -> tuple[list[Servicio], int]:
        # COUNT(*) y SELECT LIMIT/OFFSET son dos queries separadas: un INSERT/DELETE
        # concurrente entre ellas puede hacer que `total` no coincida exactamente con
        # len(items). Trade-off aceptable para paginación de lectura sin escritura muy alta.
        total: int = self.session.exec(select(func.count()).select_from(Servicio)).one()
        items = list(self.session.exec(select(Servicio).offset(offset).limit(limit)).all())
        return items, total

    def insert(self, data: ServicioCreate) -> Servicio:
        try:
            servicio = Servicio(nombre=data.nombre)
            self.session.add(servicio)
            self.session.commit()
            self.session.refresh(servicio)
            return servicio
        except Exception:
            self.session.rollback()
            raise

    def update(self, servicio_id: int, patch: ServicioPatch) -> bool:
        servicio = self.session.get(Servicio, servicio_id)
        if servicio is None:
            return False
        try:
            data = patch.model_dump(exclude_none=True)
            for key, value in data.items():
                setattr(servicio, key, value)
            self.session.add(servicio)
            self.session.commit()
            return True
        except Exception:
            self.session.rollback()
            raise

    def update_logo(self, servicio_id: int, nombre_archivo: str) -> None:
        servicio = self.session.get(Servicio, servicio_id)
        if servicio is None:
            return
        try:
            servicio.logo = nombre_archivo
            self.session.add(servicio)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def delete(self, servicio_id: int) -> bool:
        servicio = self.session.get(Servicio, servicio_id)
        if servicio is None:
            return False
        try:
            self.session.delete(servicio)
            self.session.commit()
            return True
        except Exception:
            self.session.rollback()
            raise
