"""
Modelos de dominio y esquemas de API para la entidad Grupo.

Capa arquitectónica: Dominio / Modelos de datos.

Responsabilidades:
    - Definir la tabla `grupos` en MariaDB mediante el modelo ORM `Grupo`.
    - Definir los esquemas Pydantic de entrada y salida HTTP para todas las
      operaciones CRUD sobre grupos: creación (`GrupoCreate`), lectura
      (`GrupoRead`), actualización parcial genérica (`GrupoPatch`) y
      actualización del flag superadmin (`SuperAdminPatch`).
    - Definir las tablas de asociación que implementan el modelo de permisos
      de dos niveles: permisos globales (`GrupoPermisoGlobal`) y permisos
      de sección (`GrupoSeccion`).

Qué NO debe contener este fichero:
    - Lógica de negocio ni consultas a base de datos. Eso pertenece a
      `repositories/grupo_repo.py` y `services/grupo_service.py`.
    - Definición de las entidades `Permiso` y `Seccion`. Esas tienen sus
      propios ficheros en `models/`.

Relaciones con otros módulos:
    - `core/database.py`              → registra `Grupo`, `GrupoPermisoGlobal`
                                        y `GrupoSeccion` en `SQLModel.metadata`
                                        al importar el módulo.
    - `repositories/grupo_repo.py`    → usa `Grupo` como modelo ORM y
                                        `GrupoCreate` / `GrupoRead` / `GrupoPatch`
                                        como esquemas de entrada/salida.
    - `services/grupo_service.py`     → orquesta operaciones sobre grupos usando
                                        los esquemas de este módulo.
    - `routers/grupo.py`              → declara `response_model=GrupoRead` (o
                                        listas/páginas de él) y recibe
                                        `GrupoCreate` / `GrupoPatch` como cuerpo
                                        de las peticiones.
    - `core/dependencies.py`          → consulta `GrupoSeccion` y
                                        `GrupoPermisoGlobal` para cargar los
                                        permisos del usuario en cada petición.
    - `models/permission_map.py`      → `PermissionMap[int]` agrupa permisos
                                        globales y de sección en un solo objeto
                                        incluido en `GrupoCreate` y `GrupoRead`.

Modelo de permisos de dos niveles:
    El sistema implementa un modelo de autorización de grano fino con dos capas:

    1. Permisos globales (`GrupoPermisoGlobal`):
       Un grupo puede tener permisos que aplican a todo el sistema sin restricción
       de sección. Ejemplo: permiso "ver_logs_globales".

    2. Permisos de sección (`GrupoSeccion`):
       Un grupo puede tener permisos diferentes para cada sección del sistema
       (panel, inventario, métricas, etc.). La clave primaria compuesta
       `(grupo_id, seccion_id, permiso_id)` permite que un grupo tenga múltiples
       permisos distintos en la misma sección.

    `core/dependencies.py` consulta ambas tablas para construir el `PermissionMap`
    del usuario y decidir si puede acceder a un recurso concreto.

Nombres de columnas en camelCase en la BD:
    Las columnas de clave foránea en `GrupoPermisoGlobal` y `GrupoSeccion` usan
    nombres camelCase en MariaDB (`grupoId`, `permisoId`, `seccionId`). Esto es
    un legado del sistema Java/Hibernate anterior que nombró las columnas con ese
    estilo. Se mapea explícitamente con `sa_column_kwargs={"name": "..."}`.
"""

from typing import Optional
from pydantic import ConfigDict
from pydantic.alias_generators import to_camel
from sqlmodel import Field, SQLModel
from models.permission_map import PermissionMap

# Configuración Pydantic compartida por los esquemas de entrada/salida HTTP.
# Serializa campos snake_case a camelCase en JSON (p. ej. `grupo_id` → `grupoId`)
# y permite instanciar los modelos con nombres Python originales.
_camel = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class GrupoBase(SQLModel):
    """
    Clase base con los campos de negocio del modelo ORM `Grupo`.

    No representa ninguna tabla (`table=False` por defecto). Su propósito es
    centralizar las restricciones de columna (longitud máxima, valores por
    defecto) que SQLModel usará al crear la tabla `grupos`.

    Nota: `GrupoCreate`, `GrupoRead` y `GrupoPatch` NO heredan de `GrupoBase`
    sino directamente de `SQLModel`. Esto se debe a que incluyen el campo
    `permisos` (de tipo `PermissionMap`) que no existe en la tabla ORM. Si en
    el futuro se añade un campo a `GrupoBase`, deberá añadirse manualmente
    también en los tres esquemas mencionados.

    Campos:
        nombre     (str, max 150):      Nombre identificativo del grupo.
                                        Obligatorio.
        dn         (str | None, max 512): Distinguished Name del grupo en el
                                        directorio LDAP. Si está definido, permite
                                        que el servicio LDAP autentique usuarios
                                        que pertenezcan a ese grupo del directorio.
                                        None para grupos locales sin correspondencia LDAP.
        superadmin (bool | None):       Flag de acceso irrestricto. Si es True,
                                        los usuarios de este grupo saltarán todas
                                        las comprobaciones de permiso en
                                        `core/dependencies.py`. Valor por defecto
                                        False (no superadmin).
    """

    nombre: str = Field(max_length=150)
    dn: Optional[str] = Field(default=None, max_length=512)
    superadmin: Optional[bool] = Field(default=False)


class Grupo(GrupoBase, table=True):
    """
    Modelo ORM que representa la tabla `grupos` en MariaDB.

    Con `table=True`, SQLModel registra esta clase como tabla SQLAlchemy y
    habilita su uso en queries con `Session`. Hereda todos los campos de
    `GrupoBase` y añade la clave primaria `id`.

    El `id` se declara `Optional[int]` con `default=None` para que SQLAlchemy
    pueda instanciar objetos `Grupo` sin PK antes de persistirlos (el valor
    lo asigna la BD en el INSERT).

    Atributos:
        id (int | None): Clave primaria auto-incremental asignada por la BD.
    """

    __tablename__ = "grupos"

    id: Optional[int] = Field(default=None, primary_key=True)


class GrupoCreate(SQLModel):
    """
    Esquema de entrada HTTP para la creación de un grupo (POST).

    No hereda de `GrupoBase` porque incluye el campo `permisos`, que no existe
    en la tabla ORM. Todos los campos de negocio se redeclaran aquí sin las
    restricciones de longitud de `GrupoBase` (que son restricciones de columna
    SQL, no de validación HTTP).

    El campo `permisos` permite crear un grupo y asignarle sus permisos globales
    y de sección en una única petición, evitando el patrón crear-y-luego-asignar.
    Si es `None`, el grupo se crea sin permisos.

    Campos:
        nombre     (str):                    Nombre del grupo. Obligatorio.
        dn         (str | None):             Distinguished Name LDAP. Opcional.
        superadmin (bool | None):            Flag de acceso irrestricto. Por
                                             defecto False.
        permisos   (PermissionMap[int] | None): Mapa de permisos a asignar al
                                             grupo en el momento de su creación.
                                             Los `int` son IDs de permisos de
                                             la tabla `permisos`.
    """

    model_config = _camel

    nombre: str
    dn: Optional[str] = None
    superadmin: Optional[bool] = False
    permisos: Optional[PermissionMap[int]] = None


class GrupoRead(SQLModel):
    """
    Esquema de respuesta HTTP para la lectura de un grupo (GET).

    Se usa como `response_model` en los endpoints que devuelven un grupo o
    una lista/página de grupos. Incluye el campo `permisos` para que el cliente
    reciba en una sola respuesta los datos del grupo y sus permisos asignados,
    sin necesidad de una segunda petición.

    No hereda de `GrupoBase` por el mismo motivo que `GrupoCreate`: incluye
    `permisos`, que no existe en el ORM.

    `superadmin` es `Optional[bool]` (puede ser None) a diferencia de
    `GrupoBase` donde tiene `default=False`. Esto refleja que un registro
    de grupo leído de la BD puede tener el campo NULL si fue creado antes
    de que existiera la columna.

    Campos:
        id         (int):                    Identificador único. Siempre presente.
        nombre     (str):                    Nombre del grupo.
        dn         (str | None):             Distinguished Name LDAP.
        superadmin (bool | None):            Flag de acceso irrestricto.
        permisos   (PermissionMap[int] | None): Permisos asignados al grupo.
                                             None si no se cargaron o si el grupo
                                             no tiene permisos asignados.
    """

    model_config = _camel

    id: int
    nombre: str
    dn: Optional[str] = None
    superadmin: Optional[bool] = None
    permisos: Optional[PermissionMap[int]] = None


class GrupoPatch(SQLModel):
    """
    Esquema de entrada HTTP para la actualización parcial de un grupo (PATCH).

    Todos los campos son opcionales (`None` por defecto) para soportar el
    patrón PATCH semántico: solo los campos presentes en la petición se
    actualizan; los ausentes (None) se ignoran en el repositorio.

    Campos incluidos deliberadamente:
        nombre (str | None): Nuevo nombre del grupo.
        dn     (str | None): Nuevo Distinguished Name LDAP.

    Campo excluido deliberadamente:
        `superadmin` no está en este esquema. Cambiar el flag de superadmin
        es una operación sensible que dispone de su propio endpoint y esquema
        (`SuperAdminPatch`). Separarlo previene que una actualización rutinaria
        de nombre o DN modifique accidentalmente el nivel de acceso del grupo,
        y permite aplicar controles de autorización distintos a esa operación
        específica en el router.
    """

    model_config = _camel

    nombre: Optional[str] = None
    dn: Optional[str] = None


class SuperAdminPatch(SQLModel):
    """
    Esquema de entrada HTTP para activar o desactivar el flag superadmin (PATCH).

    Esquema dedicado exclusivamente a la modificación del flag `superadmin`.
    Su existencia como clase separada responde a una decisión de seguridad:
    elevar o retirar privilegios de superadmin es una operación de alto impacto
    que merece un endpoint propio con su propia ruta, documentación OpenAPI
    diferenciada y, si se necesita en el futuro, controles de autorización
    adicionales (p. ej. requerir que el propio solicitante sea superadmin).

    Campos:
        superadmin (bool): Nuevo valor del flag. No Optional: debe ser
                           explícitamente True o False, nunca None.
    """

    model_config = _camel

    superadmin: bool


class GrupoPermisoGlobal(SQLModel, table=True):
    """
    Tabla de asociación muchos-a-muchos entre grupos y permisos globales.

    Representa el primer nivel del modelo de permisos: un permiso global aplica
    a todo el sistema sin restricción de sección. Un grupo puede tener ninguno,
    uno o varios permisos globales.

    La clave primaria es compuesta `(grupo_id, permiso_id)`, lo que garantiza
    unicidad de la relación a nivel de BD sin necesidad de un campo `id`
    adicional.

    Mapeo de nombres de columna:
        Las columnas en MariaDB usan nombres camelCase (`grupoId`, `permisoId`)
        por herencia del sistema Java/Hibernate anterior. El parámetro
        `sa_column_kwargs={"name": "..."}` indica a SQLAlchemy el nombre real
        de la columna en la BD, mientras que el campo Python mantiene el
        snake_case convencional.

    Relaciones de clave foránea:
        grupo_id  → grupos.id
        permiso_id → permisos.id
    """

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
    """
    Tabla de asociación tres-a-muchos entre grupos, secciones y permisos de sección.

    Representa el segundo nivel del modelo de permisos: un grupo tiene permisos
    diferenciados por sección del sistema. La clave primaria compuesta es
    `(grupo_id, seccion_id, permiso_id)`, lo que implica que un mismo grupo
    puede tener múltiples permisos distintos en la misma sección (p. ej. tanto
    permiso "lectura" como "escritura" para la sección "inventario").

    Esta tabla es el núcleo del control de acceso por recurso: `core/dependencies.py`
    la consulta para determinar si el usuario autenticado tiene el permiso necesario
    en la sección del endpoint que está invocando.

    Mapeo de nombres de columna:
        Las columnas en MariaDB usan nombres camelCase (`grupoId`, `seccionId`,
        `permisoId`) por herencia del sistema Java/Hibernate anterior.

    Relaciones de clave foránea:
        grupo_id   → grupos.id
        seccion_id → secciones.id
        permiso_id → permisos.id
    """

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
