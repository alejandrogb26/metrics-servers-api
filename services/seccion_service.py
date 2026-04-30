from sqlmodel import Session

from models.seccion import Seccion, SeccionCreate, SeccionPatch, SeccionRead
from repositories.seccion_repo import SeccionRepository


class SeccionService:
    def __init__(self, session: Session) -> None:
        self._repo = SeccionRepository(session)

    def find_by_id(self, seccion_id: int) -> SeccionRead | None:
        s = self._repo.find_by_id(seccion_id)
        if s is None:
            return None
        return SeccionRead(id=s.id, nombre=s.nombre, descripcion=s.descripcion)  # type: ignore[arg-type]

    def find_all(self, page: int, size: int) -> tuple[list[SeccionRead], int]:
        offset = page * size
        items, total = self._repo.find_all(offset=offset, limit=size)
        return [SeccionRead(id=s.id, nombre=s.nombre, descripcion=s.descripcion) for s in items], total  # type: ignore[arg-type]

    def insert(self, data: SeccionCreate) -> int:
        s = self._repo.insert(data)
        return s.id  # type: ignore[return-value]

    def update(self, seccion_id: int, patch: SeccionPatch) -> bool:
        return self._repo.update(seccion_id, patch)

    def delete(self, seccion_id: int) -> bool:
        return self._repo.delete(seccion_id)
