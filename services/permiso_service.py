from sqlmodel import Session

from models.permiso import PermisoRead
from repositories.permiso_repo import PermisoRepository


class PermisoService:
    def __init__(self, session: Session) -> None:
        self._repo = PermisoRepository(session)

    def get_all(self, page: int, size: int) -> tuple[list[PermisoRead], int]:
        offset = page * size
        return self._repo.find_all(offset=offset, limit=size)

    def get_by_id(self, permiso_id: int) -> PermisoRead | None:
        return self._repo.find_by_id(permiso_id)
