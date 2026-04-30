import math
from typing import Generic, Optional, TypeVar
from pydantic import BaseModel, ConfigDict, computed_field
from pydantic.alias_generators import to_camel

T = TypeVar("T")

_camel = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class PagedResponse(BaseModel, Generic[T]):
    model_config = _camel

    data: list[T]
    page: int
    size: int
    total: int

    @computed_field
    @property
    def total_pages(self) -> int:
        return math.ceil(self.total / self.size) if self.size > 0 else 0

    @computed_field
    @property
    def has_next(self) -> bool:
        return (self.page + 1) * self.size < self.total

    @classmethod
    def of(cls, items: list[T], page: int, size: int) -> "PagedResponse[T]":
        safe_size = max(1, min(size, 100))
        safe_page = max(0, page)
        total = len(items)
        start = min(safe_page * safe_size, total)
        end = min(start + safe_size, total)
        return cls(data=items[start:end], page=safe_page, size=safe_size, total=total)


class BulkResult(BaseModel):
    model_config = _camel

    total: int = 0
    ok: int = 0
    failed: int = 0
    errors: list[str] = []


class IdResponse(BaseModel):
    model_config = _camel

    id: int


class CountResult(BaseModel):
    """Respuesta estándar para operaciones de asociación/desasociación."""
    count: int


class UploadResult(BaseModel):
    """Respuesta de endpoints de subida de imagen."""
    model_config = _camel

    nombre_archivo: str
    url_foto: Optional[str] = None


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    model_config = _camel

    token: str
    token_type: str = "Bearer"
    expires_in: int
    session: Optional[dict] = None


class SessionResponse(BaseModel):
    model_config = _camel

    username: str
    display_name: Optional[str] = None
    email: Optional[str] = None
    grupo: Optional[dict] = None
    permisos: Optional[dict] = None
    url_foto: Optional[str] = None