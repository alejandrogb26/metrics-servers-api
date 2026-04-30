from typing import Optional
from pydantic import ConfigDict
from pydantic.alias_generators import to_camel
from sqlmodel import Field, SQLModel

_camel = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class UsuarioApp(SQLModel, table=True):
    __tablename__ = "usuarios_app"

    id: Optional[int] = Field(default=None, primary_key=True)
    ad_object_id: Optional[str] = Field(
        default=None, max_length=100, sa_column_kwargs={"name": "adObjectId"}
    )
    username: str = Field(max_length=100, index=True)
    foto_perfil: Optional[str] = Field(
        default=None, max_length=255, sa_column_kwargs={"name": "fotoPerfil"}
    )


class UsuarioAppRead(SQLModel):
    model_config = _camel

    id: int
    username: str
    foto_perfil: Optional[str] = None   # JSON: fotoPerfil
    url_foto: Optional[str] = None      # JSON: urlFoto
