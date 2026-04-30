from typing import Optional
from pydantic import ConfigDict
from pydantic.alias_generators import to_camel
from sqlmodel import Field, SQLModel


class Servidor(SQLModel, table=True):
    __tablename__ = "servidores"

    id: Optional[int] = Field(default=None, primary_key=True)
    server_id: str = Field(max_length=100, sa_column_kwargs={"name": "serverId"})
    dns: str = Field(max_length=255)
    hostname: Optional[str] = Field(default=None, max_length=255)
    pretty_os: Optional[str] = Field(default=None, max_length=255, sa_column_kwargs={"name": "prettyOs"})
    arch: Optional[str] = Field(default=None, max_length=50)
    kernel: Optional[str] = Field(default=None, max_length=100)
    seccion_id: int = Field(foreign_key="secciones.id", sa_column_kwargs={"name": "seccionId"})
    imagen: Optional[str] = Field(default=None, max_length=255)


class ServidorCreate(SQLModel):
    """DTO de creación. Acepta tanto camelCase (serverId, seccionId) como snake_case."""
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    server_id: str = Field(alias="serverId")
    dns: str
    seccion_id: int = Field(alias="seccionId")
    servicios: Optional[list[int]] = None


class ServidorRead(SQLModel):
    """DTO de lectura. JSON en camelCase (serverId, prettyOs, seccionId, imagenUrl)."""
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: int
    server_id: str
    dns: str
    hostname: Optional[str] = None
    pretty_os: Optional[str] = None
    arch: Optional[str] = None
    kernel: Optional[str] = None
    seccion_id: int
    imagen: Optional[str] = None
    imagen_url: Optional[str] = None
    servicios: list[int] = []


class ServidorPatch(SQLModel):
    """DTO interno. Usado por el SSH probe y el repo. No se expone directamente en la API."""
    model_config = ConfigDict(populate_by_name=True)

    server_id: Optional[str] = Field(default=None, alias="serverId")
    dns: Optional[str] = None
    hostname: Optional[str] = None
    pretty_os: Optional[str] = Field(default=None, alias="prettyOs")
    arch: Optional[str] = None
    kernel: Optional[str] = None
    seccion_id: Optional[int] = Field(default=None, alias="seccionId")
    imagen: Optional[str] = None


class ServidorPatchRequest(SQLModel):
    """DTO público para PATCH /servidor/{id}. Solo campos modificables por el usuario."""
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    server_id: Optional[str] = Field(default=None, alias="serverId")
    dns: Optional[str] = None
    seccion_id: Optional[int] = Field(default=None, alias="seccionId")


class ServidorServicio(SQLModel, table=True):
    """Tabla de asociación servidores ↔ servicios."""
    __tablename__ = "servidores_servicios"

    servidor_id: int = Field(
        primary_key=True,
        foreign_key="servidores.id",
        sa_column_kwargs={"name": "servidorId"},
    )
    servicio_id: int = Field(
        primary_key=True,
        foreign_key="servicios.id",
        sa_column_kwargs={"name": "servicioId"},
    )
