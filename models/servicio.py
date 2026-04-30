from typing import Optional
from pydantic import ConfigDict
from pydantic.alias_generators import to_camel
from sqlmodel import Field, SQLModel

_camel = ConfigDict(alias_generator=to_camel, populate_by_name=True)
_camel_strict = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class ServicioBase(SQLModel):
    """Campos públicos de un servicio. `logo` es interno y no forma parte de esta base."""
    nombre: str = Field(max_length=100)


class Servicio(ServicioBase, table=True):
    __tablename__ = "servicios"

    id: Optional[int] = Field(default=None, primary_key=True)
    logo: Optional[str] = Field(default=None, max_length=255)  # gestionado vía POST /{id}/logo


class ServicioCreate(ServicioBase):
    model_config = _camel_strict


class ServicioRead(ServicioBase):
    model_config = _camel

    id: int
    url_logo: Optional[str] = None   # JSON: urlLogo


class ServicioPatch(SQLModel):
    model_config = _camel_strict

    nombre: Optional[str] = None
