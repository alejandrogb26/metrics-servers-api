"""
Modelos de dominio y esquemas de API para la entidad Servicio.

Capa arquitectónica: Dominio / Modelos de datos.

Responsabilidades:
    - Definir la tabla `servicios` en MariaDB mediante el modelo ORM `Servicio`.
    - Definir los esquemas Pydantic para las operaciones CRUD sobre servicios:
      creación (`ServicioCreate`), lectura (`ServicioRead`) y actualización
      parcial (`ServicioPatch`).
    - Separar la gestión del logo del ciclo de vida normal del servicio:
      el campo `logo` existe en el ORM pero se excluye de los esquemas de
      creación y actualización genérica, reservándolo a un endpoint dedicado.

Qué NO debe contener este fichero:
    - Lógica de negocio ni acceso a base de datos.
    - Generación de URLs de logo. La transformación de nombre de fichero
      (`logo`) a URL pública (`url_logo`) ocurre en la capa de servicio o
      repositorio, no aquí.

Relaciones con otros módulos:
    - `core/database.py`           → registra `Servicio` en `SQLModel.metadata`
                                     al importar el módulo.
    - `models/servidor.py`         → los servidores pueden estar asociados a
                                     uno o varios servicios.
    - `services/minio_service.py`  → gestiona la subida del logo y devuelve
                                     la URL que se incluye en `ServicioRead.url_logo`.
    - Repositorios y routers       → usan `Servicio` como ORM y `ServicioRead`
                                     como esquema de respuesta.

Gestión del logo:
    El campo `logo` del ORM almacena el nombre del fichero en el bucket de
    MinIO. No forma parte de `ServicioBase` ni de los esquemas de creación
    o edición genérica: se actualiza exclusivamente a través del endpoint
    `POST /{id}/logo`, que invoca `services/minio_service.py` para subir
    la imagen y actualizar el campo directamente.

    En la respuesta HTTP (`ServicioRead`), el campo se expone como `url_logo`
    (la URL pública del fichero en MinIO), no como el nombre interno del
    fichero. Esta transformación la realiza la capa de servicio/repositorio
    antes de construir el `ServicioRead`.

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

# Configuración camelCase estándar para esquemas de respuesta (lectura).
# No restringe campos adicionales porque FastAPI ya filtra la salida según
# el response_model declarado en el router.
_camel = ConfigDict(alias_generator=to_camel, populate_by_name=True)

# Configuración camelCase estricta para esquemas de entrada (creación y PATCH).
# `extra="forbid"` rechaza cualquier campo no declarado en el esquema con un
# error de validación 422. Esto previene que los clientes envíen campos
# inesperados (p. ej. `logo`) por un error de integración, y hace explícito
# el contrato de entrada de cada operación.
_camel_strict = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class ServicioBase(SQLModel):
    """
    Clase base con los campos públicos de la entidad Servicio.

    Solo contiene `nombre`. El campo `logo` se excluye deliberadamente de
    esta base porque su ciclo de vida es independiente del CRUD estándar:
    se gestiona a través de un endpoint dedicado (`POST /{id}/logo`) y
    no debe ser accesible como campo de creación ni de actualización genérica.

    Campos:
        nombre (str, max 100): Nombre identificativo del servicio. Obligatorio.
    """

    nombre: str = Field(max_length=100)


class Servicio(ServicioBase, table=True):
    """
    Modelo ORM que representa la tabla `servicios` en MariaDB.

    Con `table=True`, SQLModel registra esta clase como tabla SQLAlchemy.
    Añade la clave primaria `id` y el campo interno `logo`, que no aparece
    en `ServicioBase` ni en los esquemas HTTP de entrada.

    Atributos:
        id   (int | None):  Clave primaria auto-incremental. `None` antes de
                            la primera persistencia; entero positivo tras el commit.
        logo (str | None):  Nombre del fichero de logo almacenado en MinIO.
                            `None` si el servicio no tiene logo asignado.
                            Se actualiza exclusivamente vía `POST /{id}/logo`;
                            no está expuesto en los esquemas de creación ni de
                            actualización genérica.
    """

    __tablename__ = "servicios"

    id: Optional[int] = Field(default=None, primary_key=True)
    logo: Optional[str] = Field(default=None, max_length=255)  # gestionado vía POST /{id}/logo


class ServicioCreate(ServicioBase):
    """
    Esquema de entrada HTTP para la creación de un servicio (POST).

    Hereda `nombre` de `ServicioBase` y añade la configuración `_camel_strict`,
    que aplica `extra="forbid"` para rechazar campos no declarados. Esto garantiza
    que el cliente no puede enviar `logo` u otros campos internos en la petición
    de creación; el logo se asigna posteriormente mediante su endpoint dedicado.

    El uso de `_camel_strict` sobre `_camel` en esquemas de entrada es una
    práctica defensiva: hace el contrato explícito y devuelve un error 422
    descriptivo en lugar de ignorar silenciosamente los campos desconocidos.
    """

    model_config = _camel_strict


class ServicioRead(ServicioBase):
    """
    Esquema de respuesta HTTP para la lectura de un servicio (GET).

    Hereda `nombre` de `ServicioBase` y añade `id` (requerido) y `url_logo`
    (opcional). Usa `_camel` (sin `extra="forbid"`) porque en las respuestas
    no tiene sentido restringir campos adicionales: FastAPI ya filtra la salida
    según el `response_model`.

    Transformación `logo` → `url_logo`:
        El ORM almacena en `logo` el nombre del fichero en MinIO. La respuesta
        HTTP expone `url_logo`, que es la URL pública (o firmada) con la que el
        cliente puede cargar la imagen. Esta transformación ocurre en la capa de
        servicio o repositorio antes de construir este esquema; el nombre interno
        del fichero no se expone al cliente.

    Campos adicionales respecto a `ServicioBase`:
        id      (int):         Identificador único. Siempre presente en respuestas.
        url_logo (str | None): URL pública del logo en MinIO. `None` si el
                               servicio no tiene logo asignado. Serializa como
                               `urlLogo` en el JSON de respuesta (camelCase).
    """

    model_config = _camel

    id: int
    url_logo: Optional[str] = None   # JSON: urlLogo


class ServicioPatch(SQLModel):
    """
    Esquema de entrada HTTP para la actualización parcial de un servicio (PATCH).

    No hereda de `ServicioBase` porque todos los campos deben ser opcionales
    para el patrón PATCH semántico. Usa `_camel_strict` para rechazar campos
    no declarados (misma razón que en `ServicioCreate`): previene que un cliente
    intente modificar `logo` directamente a través de este endpoint.

    Solo permite modificar `nombre`. El logo tiene su propio endpoint dedicado.

    Campos:
        nombre (str | None): Nuevo nombre del servicio. `None` = no actualizar.
    """

    model_config = _camel_strict

    nombre: Optional[str] = None
