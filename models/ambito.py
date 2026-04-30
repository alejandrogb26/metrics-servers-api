from typing import Optional
from pydantic import ConfigDict
from pydantic.alias_generators import to_camel
from sqlmodel import Field, SQLModel

_camel = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class AmbitoBase(SQLModel):
    nombre: str = Field(max_length=100)
    descripcion: Optional[str] = Field(default=None, max_length=255)


class Ambito(AmbitoBase, table=True):
    __tablename__ = "ambitos"

    id: Optional[int] = Field(default=None, primary_key=True)


class AmbitoRead(AmbitoBase):
    model_config = _camel

    id: int