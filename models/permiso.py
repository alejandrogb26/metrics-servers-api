from typing import Optional
from pydantic import ConfigDict
from pydantic.alias_generators import to_camel
from sqlmodel import Field, SQLModel
from models.ambito import AmbitoRead

_camel = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class PermisoBase(SQLModel):
    nombre: str = Field(max_length=100)
    descripcion: Optional[str] = Field(default=None, max_length=255)
    # sa_column_kwargs={"name": "ambitoId"} mapea al nombre real de columna en BD
    ambito_id: int = Field(
        foreign_key="ambitos.id",
        sa_column_kwargs={"name": "ambitoId"},
    )


class Permiso(PermisoBase, table=True):
    __tablename__ = "permisos"

    id: Optional[int] = Field(default=None, primary_key=True)


class PermisoRead(SQLModel):
    model_config = _camel

    id: int
    nombre: str
    descripcion: Optional[str] = None
    ambito: AmbitoRead
