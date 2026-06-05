"""
Modelos de dominio y esquemas de API para la entidad Permiso.

Capa arquitectónica: Dominio / Modelos de datos.

Responsabilidades:
    - Definir la tabla `permisos` en MariaDB mediante el modelo ORM `Permiso`.
    - Definir los esquemas Pydantic usados en las respuestas HTTP que devuelven
      permisos (`PermisoRead`), incluyendo el ámbito completo embebido.

Qué NO debe contener este fichero:
    - Lógica de negocio ni consultas a base de datos.
    - Operaciones de asignación de permisos a grupos. Esas se modelan en
      `models/grupo.py` (`GrupoPermisoGlobal`, `GrupoSeccion`) y se ejecutan
      en `repositories/grupo_repo.py`.

Relaciones con otros módulos:
    - `models/ambito.py`           → `PermisoRead` embebe un `AmbitoRead`
                                     completo en lugar de devolver solo el FK.
    - `models/grupo.py`            → `GrupoPermisoGlobal` y `GrupoSeccion`
                                     referencian `permisos.id` como FK para
                                     asignar permisos a grupos.
    - `core/database.py`           → registra `Permiso` en `SQLModel.metadata`
                                     al importar el módulo.
    - `core/dependencies.py`       → carga los IDs de permisos del grupo del
                                     usuario para validar el acceso a cada
                                     endpoint protegido.
    - Repositorios y routers       → usan `Permiso` como ORM y `PermisoRead`
                                     como esquema de respuesta.

Rol de `Permiso` en el sistema de autorización:
    Un permiso es un derecho con nombre (p. ej. "ver_servidores", "editar_grupos")
    que pertenece a un ámbito (`Ambito`). Los grupos reciben permisos asignados
    a dos niveles (ver `models/grupo.py`):
        - Nivel global: el permiso aplica sin restricción de sección.
        - Nivel de sección: el permiso aplica solo en una sección concreta.
    `core/dependencies.py` consulta ambos niveles para decidir si el usuario
    puede invocar un endpoint determinado.
"""

from typing import Optional
from pydantic import ConfigDict
from pydantic.alias_generators import to_camel
from sqlmodel import Field, SQLModel
from models.ambito import AmbitoRead

# Configuración Pydantic compartida por los esquemas de respuesta HTTP.
# Serializa snake_case a camelCase en JSON y permite instanciar con nombre
# Python original o con alias camelCase.
_camel = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class PermisoBase(SQLModel):
    """
    Clase base con los campos de negocio del modelo ORM `Permiso`.

    No representa ninguna tabla (`table=False` por defecto). Centraliza las
    restricciones de columna compartidas entre `Permiso` (ORM) y la futura
    extensión de esquemas de escritura.

    Nota: `PermisoRead` NO hereda de `PermisoBase`. En la respuesta HTTP el
    campo `ambito_id` (FK entero) se reemplaza por `ambito` (objeto `AmbitoRead`
    completo), lo que requiere una estructura distinta.

    Campos:
        nombre     (str, max 100):      Nombre identificativo del permiso.
                                        Obligatorio.
        descripcion (str | None, max 255): Descripción opcional del permiso.
        ambito_id  (int):               FK a `ambitos.id`. Indica a qué ámbito
                                        pertenece el permiso, lo que define su
                                        alcance semántico en el sistema de
                                        autorización. La columna en MariaDB se
                                        llama `ambitoId` (camelCase heredado del
                                        sistema Java anterior).
    """

    nombre: str = Field(max_length=100)
    descripcion: Optional[str] = Field(default=None, max_length=255)
    # sa_column_kwargs={"name": "ambitoId"} mapea al nombre real de columna en BD
    ambito_id: int = Field(
        foreign_key="ambitos.id",
        sa_column_kwargs={"name": "ambitoId"},
    )


class Permiso(PermisoBase, table=True):
    """
    Modelo ORM que representa la tabla `permisos` en MariaDB.

    Con `table=True`, SQLModel registra esta clase como tabla SQLAlchemy.
    Hereda todos los campos de `PermisoBase` y añade la clave primaria `id`.

    Los registros de esta tabla son relativamente estáticos: los permisos se
    crean durante la configuración inicial del sistema y raramente se añaden
    o eliminan en producción. Los cambios dinámicos de autorización se hacen
    asignando o quitando permisos a grupos, no creando nuevos permisos.

    Atributos:
        id (int | None): Clave primaria auto-incremental. `None` antes de la
                         primera persistencia; entero positivo tras el commit.
    """

    __tablename__ = "permisos"

    id: Optional[int] = Field(default=None, primary_key=True)


class PermisoRead(SQLModel):
    """
    Esquema de respuesta HTTP para la lectura de un permiso (GET).

    No hereda de `PermisoBase` porque sustituye el campo `ambito_id: int`
    (FK numérico) por `ambito: AmbitoRead` (objeto completo embebido). Esto
    evita que el cliente tenga que hacer una segunda petición para resolver
    el nombre y descripción del ámbito al que pertenece el permiso.

    Este patrón de embeber el objeto relacionado completo es coherente con
    la filosofía REST de devolver representaciones autocontenidas: el cliente
    recibe toda la información necesaria para mostrar el permiso en un solo
    round-trip.

    Campos:
        id          (int):        Identificador único del permiso. Siempre presente.
        nombre      (str):        Nombre del permiso.
        descripcion (str | None): Descripción del permiso.
        ambito      (AmbitoRead): Ámbito al que pertenece el permiso, con su
                                  `id` y `nombre` completos. Nunca None: todo
                                  permiso debe tener un ámbito asignado.
    """

    model_config = _camel

    id: int
    nombre: str
    descripcion: Optional[str] = None
    ambito: AmbitoRead
