"""
Modelos de dominio y esquemas de API para la entidad Sección.

Capa arquitectónica: Dominio / Modelos de datos.

Responsabilidades:
    - Definir la tabla `secciones` en MariaDB mediante el modelo ORM `Seccion`.
    - Definir los esquemas Pydantic para las operaciones CRUD sobre secciones:
      creación (`SeccionCreate`), lectura (`SeccionRead`) y actualización
      parcial (`SeccionPatch`).

Qué NO debe contener este fichero:
    - Lógica de negocio ni consultas a base de datos.
    - La asociación entre secciones y grupos. Esa se modela en `models/grupo.py`
      (`GrupoSeccion`) y se gestiona en `repositories/grupo_repo.py`.

Relaciones con otros módulos:
    - `core/database.py`           → registra `Seccion` en `SQLModel.metadata`
                                     al importar el módulo.
    - `models/grupo.py`            → `GrupoSeccion` referencia `secciones.id`
                                     como FK; las secciones son el eje de los
                                     permisos de segundo nivel del sistema de
                                     autorización.
    - `core/dependencies.py`       → compara `seccion_id` al evaluar si el
                                     usuario tiene el permiso requerido en la
                                     sección del endpoint solicitado.
    - Repositorios y routers       → usan `Seccion` como ORM y `SeccionRead`
                                     como esquema de respuesta.

Rol de `Seccion` en el sistema de autorización:
    Una sección representa un área funcional de la aplicación (p. ej.
    "inventario", "métricas", "configuración"). Los grupos reciben permisos
    diferenciados por sección mediante la tabla `GrupoSeccion`: un grupo puede
    tener permiso de lectura en "métricas" pero no en "configuración". Esto
    constituye el segundo nivel del modelo de permisos (ver `models/grupo.py`).
"""

from typing import Optional
from pydantic import ConfigDict
from pydantic.alias_generators import to_camel
from sqlmodel import Field, SQLModel

# Configuración Pydantic compartida por los esquemas de entrada/salida HTTP.
# Serializa snake_case a camelCase en JSON y permite instanciar con el nombre
# Python original o con el alias camelCase.
_camel = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class SeccionBase(SQLModel):
    """
    Clase base con los campos de negocio compartidos de la entidad Sección.

    No representa ninguna tabla (`table=False` por defecto). Centraliza la
    definición de campos y restricciones para que los esquemas de creación
    y lectura los hereden sin duplicación.

    A diferencia de `GrupoBase`, aquí sí es posible que los esquemas HTTP
    hereden de la base porque la entidad no necesita campos adicionales (como
    `permisos`) que rompan la jerarquía. `SeccionCreate` y `SeccionRead`
    heredan directamente de esta clase.

    Campos:
        nombre      (str, max 100):      Nombre identificativo de la sección.
                                         Obligatorio.
        descripcion (str | None, max 255): Descripción opcional de la sección.
    """

    nombre: str = Field(max_length=100)
    descripcion: Optional[str] = Field(default=None, max_length=255)


class Seccion(SeccionBase, table=True):
    """
    Modelo ORM que representa la tabla `secciones` en MariaDB.

    Con `table=True`, SQLModel registra esta clase como tabla SQLAlchemy.
    Hereda todos los campos de `SeccionBase` y añade la clave primaria `id`.

    El `id` se declara `Optional[int]` con `default=None` para que SQLAlchemy
    pueda instanciar objetos `Seccion` sin PK antes de persistirlos (el valor
    lo asigna la BD en el INSERT automáticamente).

    Atributos:
        id (int | None): Clave primaria auto-incremental asignada por la BD.
    """

    __tablename__ = "secciones"

    id: Optional[int] = Field(default=None, primary_key=True)


class SeccionCreate(SeccionBase):
    """
    Esquema de entrada HTTP para la creación de una sección (POST).

    Hereda todos los campos de `SeccionBase` (`nombre` obligatorio,
    `descripcion` opcional) y añade únicamente la configuración de
    serialización camelCase. No añade ni modifica ningún campo.

    FastAPI usa este esquema para validar y deserializar el cuerpo JSON
    de la petición de creación antes de pasarlo al servicio.
    """

    model_config = _camel


class SeccionRead(SeccionBase):
    """
    Esquema de respuesta HTTP para la lectura de una sección (GET).

    Hereda los campos de `SeccionBase` y añade `id` como campo requerido
    (no `Optional`): en una respuesta HTTP una sección siempre tiene
    identificador asignado por la BD.

    Se usa como `response_model` en los endpoints que devuelven una sección
    individual o una lista/página de secciones.

    Campos adicionales respecto a `SeccionBase`:
        id (int): Identificador único de la sección. Siempre presente en
                  respuestas; nunca None.
    """

    model_config = _camel

    id: int


class SeccionPatch(SQLModel):
    """
    Esquema de entrada HTTP para la actualización parcial de una sección (PATCH).

    No hereda de `SeccionBase` porque todos los campos deben ser opcionales
    para soportar el patrón PATCH semántico: solo los campos presentes en la
    petición se actualizan; los ausentes (None) se ignoran en el repositorio.
    En `SeccionBase`, `nombre` es obligatorio (sin `default`), lo que impediría
    heredar y hacer todos los campos opcionales sin redeclararlos.

    Campos:
        nombre      (str | None): Nuevo nombre de la sección. None = no actualizar.
        descripcion (str | None): Nueva descripción. None = no actualizar.
    """

    model_config = _camel

    nombre: Optional[str] = None
    descripcion: Optional[str] = None
