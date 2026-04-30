from sqlmodel import Session

from models.ambito import AmbitoRead
from repositories.ambito_repo import AmbitoRepository


class AmbitoService:
    def __init__(self, session: Session) -> None:
        self._repo = AmbitoRepository(session)

    def get_all(self, page: int, size: int) -> tuple[list[AmbitoRead], int]:
        offset = page * size
        items, total = self._repo.find_all(offset=offset, limit=size)
        return [AmbitoRead(id=a.id, nombre=a.nombre, descripcion=a.descripcion) for a in items], total  # type: ignore[arg-type]

    def get_by_id(self, ambito_id: int) -> AmbitoRead | None:
        a = self._repo.find_by_id(ambito_id)
        if a is None:
            return None
        return AmbitoRead(id=a.id, nombre=a.nombre, descripcion=a.descripcion)  # type: ignore[arg-type]
