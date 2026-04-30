from typing import Generic, Optional, TypeVar
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

T = TypeVar("T")


class PermissionMap(BaseModel, Generic[T]):
    """
    Agrupa permisos globales y permisos por sección.

    - globalPerms (global_perms): permisos aplicables a todo el sistema
    - sections: mapa seccionId → lista de permisos
    """
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    global_perms: Optional[list[T]] = None
    sections: Optional[dict[int, list[T]]] = None
