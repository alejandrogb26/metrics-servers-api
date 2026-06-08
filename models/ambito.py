"""
Modelos de dominio y esquemas de API para la entidad Ámbito.

Capa arquitectónica: Dominio / Modelos de datos.

Responsabilidades:
    - Definir la tabla `ambitos` en MariaDB mediante el modelo ORM `Ambito`
      (SQLModel con `table=True`).
    - Definir los esquemas Pydantic usados para validar y serializar los datos
      de la entidad en las peticiones y respuestas HTTP (`AmbitoBase`, `AmbitoRead`).
    - Compartir los campos comunes entre el modelo ORM y los esquemas de API
      mediante la clase base `AmbitoBase`, evitando duplicación.

Qué NO debe contener este fichero:
    - Lógica de negocio ni de validación compleja. Solo definición de campos
      y restricciones de formato (longitud máxima, opcionalidad).
    - Consultas a base de datos ni operaciones de persistencia. Eso pertenece
      a los repositorios.
    - Relaciones SQLAlchemy (`Relationship`) con otras tablas. Si en el futuro
      se añaden, deben documentarse explícitamente aquí.

Relaciones con otros módulos:
    - `core/database.py`               → `SQLModel.metadata` registra `Ambito`
                                         cuando se importa el módulo; `create_db_tables`
                                         usa esa metadata para crear la tabla.
    - Repositorios de ámbitos          → usan `Ambito` como modelo ORM para
                                         queries y `AmbitoRead` como esquema de
                                         respuesta serializada.
    - Routers de ámbitos               → declaran `response_model=AmbitoRead` (o
                                         `list[AmbitoRead]`) para que FastAPI
                                         serialice y valide la respuesta automáticamente.
    - `models/grupo.py`                → los grupos pueden estar asociados a un
                                         ámbito; ver ese módulo para la relación
                                         entre entidades.

Patrón de herencia de modelos (SQLModel):
    SQLModel unifica Pydantic y SQLAlchemy en una sola jerarquía de clases.
    El patrón de tres niveles usado aquí es el recomendado por la documentación
    oficial de SQLModel para evitar duplicar la definición de campos:

        AmbitoBase (SQLModel, table=False)
            ├─ Ambito (AmbitoBase, table=True)   ← modelo ORM / tabla real en BD
            └─ AmbitoRead (AmbitoBase)            ← esquema Pydantic de respuesta HTTP

    `AmbitoBase` concentra los campos de negocio compartidos.
    `Ambito` añade solo el campo `id` con la anotación de clave primaria para SQLAlchemy.
    `AmbitoRead` añade `id` como campo requerido (no `Optional`) y la configuración
    de serialización camelCase para la API.

Autor:
    Alejandro Gómez Blanco

Proyecto:
    Metrics Servers

Versión:
    1.0.0

Organización:
    Metrics Servers Project
"""

from typing import Optional
from pydantic import ConfigDict
from pydantic.alias_generators import to_camel
from sqlmodel import Field, SQLModel

# Configuración Pydantic compartida por los esquemas de respuesta HTTP.
# - `alias_generator=to_camel`: convierte automáticamente los nombres de campo
#   de snake_case Python a camelCase en el JSON de respuesta (p. ej. un futuro
#   campo `grupo_id` se serializa como `grupoId`). Los clientes Flutter y Swing
#   esperan camelCase.
# - `populate_by_name=True`: permite instanciar el modelo usando tanto el nombre
#   Python original (`nombre`) como el alias camelCase (`nombre`). Necesario para
#   que SQLModel pueda construir el esquema a partir de datos ORM sin requerir que
#   la BD use también camelCase.
_camel = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class AmbitoBase(SQLModel):
    """
    Clase base con los campos de negocio compartidos de la entidad Ámbito.

    No representa una tabla en la base de datos (`table=False` por defecto en
    SQLModel). Su único propósito es centralizar la definición de los campos
    comunes para que `Ambito` (ORM) y `AmbitoRead` (esquema de respuesta)
    los hereden sin duplicación.

    Los campos definidos aquí establecen las restricciones de formato que
    SQLModel aplica tanto al crear la columna en MariaDB (longitud máxima)
    como al validar los datos entrantes con Pydantic.

    Campos:
        nombre      (str, max 100): Nombre identificativo del ámbito.
                                    Obligatorio. Columna VARCHAR(100) en BD.
        descripcion (str | None, max 255): Descripción opcional del ámbito.
                                    NULL en BD si no se proporciona.
    """

    nombre: str = Field(max_length=100)
    descripcion: Optional[str] = Field(default=None, max_length=255)


class Ambito(AmbitoBase, table=True):
    """
    Modelo ORM que representa la tabla `ambitos` en MariaDB.

    Al heredar de `AmbitoBase` con `table=True`, SQLModel activa el modo
    ORM: la clase se registra en `SQLModel.metadata` como una tabla de
    SQLAlchemy y puede usarse en queries con `Session`.

    El campo `id` se declara `Optional[int]` con `default=None` deliberadamente:
    SQLAlchemy necesita poder instanciar objetos `Ambito` sin asignar un `id`
    antes de persistirlos (la BD asigna el valor auto-incremental al hacer
    commit). Si `id` fuera `int` sin default, construir un nuevo ámbito antes
    de guardarlo requeriría un valor de PK ficticio.

    Atributos:
        id (int | None): Clave primaria auto-incremental. `None` antes de la
                         primera persistencia; entero positivo después del commit.
    """

    __tablename__ = "ambitos"

    id: Optional[int] = Field(default=None, primary_key=True)


class AmbitoRead(AmbitoBase):
    """
    Esquema Pydantic de respuesta HTTP para la entidad Ámbito.

    Se usa como `response_model` en los endpoints que devuelven uno o varios
    ámbitos. FastAPI utiliza este esquema para serializar el objeto ORM `Ambito`
    a JSON y para generar la documentación OpenAPI de la respuesta.

    Diferencias respecto a `Ambito` (ORM):
        - `id` es `int` (no `Optional[int]`): en una respuesta HTTP, el ámbito
          siempre tiene un identificador asignado por la BD. Si se devolviera
          un ámbito sin `id`, indicaría un error en la lógica del repositorio.
        - Incluye `model_config = _camel` para serializar los campos en camelCase
          en el JSON de respuesta. Los clientes Flutter y Swing esperan este
          formato.
        - No tiene la anotación `table=True`, por lo que no registra ninguna
          tabla en SQLAlchemy.

    Atributos:
        id (int): Identificador único del ámbito. Siempre presente en respuestas.
    """

    model_config = _camel

    id: int
