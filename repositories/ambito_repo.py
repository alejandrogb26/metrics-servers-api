from sqlalchemy import func
from sqlmodel import Session, select
from models.ambito import Ambito


class AmbitoRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find_by_id(self, ambito_id: int) -> Ambito | None:
        return self.session.get(Ambito, ambito_id)

    def find_all(self, offset: int, limit: int) -> tuple[list[Ambito], int]:
        # COUNT(*) y SELECT LIMIT/OFFSET son dos queries separadas: un INSERT/DELETE
        # concurrente entre ellas puede hacer que `total` no coincida exactamente con
        # len(items). Trade-off aceptable para paginación de lectura sin escritura muy alta.
        total: int = self.session.exec(select(func.count()).select_from(Ambito)).one()
        items = list(self.session.exec(select(Ambito).offset(offset).limit(limit)).all())
        return items, total
