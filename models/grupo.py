from typing import Optional
from pydantic import ConfigDict
from pydantic.alias_generators import to_camel
from sqlmodel import Field, SQLModel
from models.permission_map import PermissionMap

_camel = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class GrupoBase(SQLModel):
    nombre: str = Field(max_length=150)
    dn: Optional[str] = Field(default=None, max_length=512)
    superadmin: Optional[bool] = Field(default=False)


class Grupo(GrupoBase, table=True):
    __tablename__ = "grupos"

    id: Optional[int] = Field(default=None, primary_key=True)


class GrupoCreate(SQLModel):
    model_config = _camel

    nombre: str
    dn: Optional[str] = None
    superadmin: Optional[bool] = False
    permisos: Optional[PermissionMap[int]] = None


class GrupoRead(SQLModel):
    model_config = _camel

    id: int
    nombre: str
    dn: Optional[str] = None
    superadmin: Optional[bool] = None
    permisos: Optional[PermissionMap[int]] = None


class GrupoPatch(SQLModel):
    model_config = _camel

    nombre: Optional[str] = None


class SuperAdminPatch(SQLModel):
    model_config = _camel

    superadmin: bool


class GrupoPermisoGlobal(SQLModel, table=True):
    """Tabla de asociación grupos ↔ permisos globales."""
    __tablename__ = "grupo_permiso_global"

    grupo_id: int = Field(
        primary_key=True,
        foreign_key="grupos.id",
        sa_column_kwargs={"name": "grupoId"},
    )
    permiso_id: int = Field(
        primary_key=True,
        foreign_key="permisos.id",
        sa_column_kwargs={"name": "permisoId"},
    )


class GrupoSeccion(SQLModel, table=True):
    """Tabla de asociación grupos ↔ secciones ↔ permisos de sección."""
    __tablename__ = "grupo_seccion"

    grupo_id: int = Field(
        primary_key=True,
        foreign_key="grupos.id",
        sa_column_kwargs={"name": "grupoId"},
    )
    seccion_id: int = Field(
        primary_key=True,
        foreign_key="secciones.id",
        sa_column_kwargs={"name": "seccionId"},
    )
    permiso_id: int = Field(
        primary_key=True,
        foreign_key="permisos.id",
        sa_column_kwargs={"name": "permisoId"},
    )