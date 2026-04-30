from typing import Optional
from pydantic import ConfigDict
from pydantic.alias_generators import to_camel
from sqlmodel import Field, SQLModel

_camel = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class SeccionBase(SQLModel):
    nombre: str = Field(max_length=100)
    descripcion: Optional[str] = Field(default=None, max_length=255)


class Seccion(SeccionBase, table=True):
    __tablename__ = "secciones"

    id: Optional[int] = Field(default=None, primary_key=True)


class SeccionCreate(SeccionBase):
    model_config = _camel


class SeccionRead(SeccionBase):
    model_config = _camel

    id: int


class SeccionPatch(SQLModel):
    model_config = _camel

    nombre: Optional[str] = None
    descripcion: Optional[str] = None
