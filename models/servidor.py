"""
Modelos de dominio y esquemas de API para la entidad Servidor.

Capa arquitectónica: Dominio / Modelos de datos.

Responsabilidades:
    - Definir la tabla `servidores` en MariaDB mediante el modelo ORM `Servidor`.
    - Definir los esquemas Pydantic para las operaciones sobre servidores:
      creación (`ServidorCreate`), lectura (`ServidorRead`), actualización
      interna por el sondeo SSH (`ServidorPatch`) y actualización pública
      por el usuario (`ServidorPatchRequest`).
    - Definir la tabla de asociación `servidores_servicios` que vincula
      servidores con los servicios que ejecutan (`ServidorServicio`).

Qué NO debe contener este fichero:
    - Lógica de negocio, sondeo SSH ni acceso a base de datos.
    - Generación de URLs de imagen. La transformación `imagen` → `imagen_url`
      ocurre en la capa de servicio o repositorio.

Relaciones con otros módulos:
    - `core/database.py`              → registra `Servidor` y `ServidorServicio`
                                        en `SQLModel.metadata`.
    - `models/seccion.py`             → `Servidor.seccion_id` FK a `secciones.id`.
                                        La sección determina qué grupo de usuarios
                                        puede ver/gestionar el servidor.
    - `models/servicio.py`            → `ServidorServicio` vincula servidores con
                                        servicios mediante `servicios.id`.
    - `services/servidor_service.py`  → orquesta la creación, lectura y update de
                                        servidores, incluida la generación de
                                        `imagen_url` para `ServidorRead`.
    - `services/servidor_service.py`  → el sondeo SSH usa `ServidorPatch` para
                                        actualizar los campos de sistema descubiertos
                                        (`hostname`, `pretty_os`, `arch`, `kernel`).
    - `repositories/servidor_repo.py` → usa `Servidor` como ORM y los DTOs
                                        de este módulo para las operaciones CRUD.

Separación entre campos de usuario y campos del sondeo SSH:
    El servidor tiene dos categorías de campos actualizables:

    1. Campos gestionados por el usuario (vía `ServidorPatchRequest`):
       `server_id`, `dns`, `seccion_id`. Son datos de inventario que el
       administrador introduce o corrige manualmente.

    2. Campos gestionados por el sondeo SSH (vía `ServidorPatch`):
       `hostname`, `pretty_os`, `arch`, `kernel`. Son datos de sistema
       que la API descubre automáticamente al conectarse al servidor por SSH.
       Los usuarios no pueden sobrescribirlos directamente.

    Esta separación es un diseño deliberado de seguridad y coherencia: los
    datos descubiertos automáticamente no deben ser modificables por usuarios
    arbitrarios, y el sondeo no debe interferir con los campos de inventario.

Nombres de columnas en camelCase en la BD:
    Varios campos usan `sa_column_kwargs={"name": "..."}` para mapear el nombre
    Python snake_case al nombre camelCase de la columna en MariaDB, heredado
    del sistema Java/Hibernate anterior: `serverId`, `prettyOs`, `seccionId`.

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


class Servidor(SQLModel, table=True):
    """
    Modelo ORM que representa la tabla `servidores` en MariaDB.

    No tiene clase base separada (`ServicioBase`, `SeccionBase`, etc.) porque
    la entidad Servidor es suficientemente específica y no comparte campos con
    ningún otro modelo.

    Campos:
        id         (int | None): Clave primaria auto-incremental de la BD.
                                 `None` antes del primer commit.
        server_id  (str, max 100): Identificador externo del servidor (distinto
                                 del PK interno `id`). Puede provenir de un
                                 agente de monitorización o sistema de inventario.
                                 Columna `serverId` en BD (camelCase).
        dns        (str, max 255): FQDN o dirección IP del servidor. Usado por el
                                 sondeo SSH para establecer la conexión.
        hostname   (str | None, max 255): Hostname reportado por el propio servidor
                                 vía SSH. Puede diferir del `dns` si el servidor
                                 tiene múltiples nombres. Actualizado por el sondeo.
        pretty_os  (str | None, max 255): Nombre legible del sistema operativo
                                 (p. ej. "Ubuntu 22.04 LTS"). Descubierto por SSH.
                                 Columna `prettyOs` en BD.
        arch       (str | None, max 50):  Arquitectura de CPU (p. ej. "x86_64").
                                 Descubierta por SSH.
        kernel     (str | None, max 100): Versión del kernel del SO. Descubierta
                                 por SSH.
        seccion_id (int):        FK a `secciones.id`. Determina en qué sección
                                 está clasificado el servidor y, por tanto, qué
                                 grupos de usuarios tienen permisos sobre él.
                                 Columna `seccionId` en BD.
        imagen     (str | None, max 255): Nombre del fichero de imagen del
                                 servidor en MinIO. Gestionado vía su endpoint
                                 dedicado de subida.
    """

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
    """
    DTO de entrada HTTP para la creación de un servidor (POST).

    Acepta los campos tanto en camelCase (aliases explícitos: `serverId`,
    `seccionId`) como en snake_case (`server_id`, `seccion_id`) gracias a
    `populate_by_name=True`. A diferencia de los modelos de otras entidades,
    usa aliases explícitos (`Field(alias=...)`) en lugar de `alias_generator=to_camel`
    porque solo dos campos necesitan alias; el resto (`dns`, `servicios`) no
    requieren conversión.

    `extra="forbid"` rechaza campos desconocidos, previniendo que el cliente
    envíe por error campos de sistema (`hostname`, `pretty_os`, etc.) en la
    creación.

    Campos de usuario (los únicos aceptados en creación):
        server_id  (str):               Identificador externo del servidor.
        dns        (str):               FQDN o IP del servidor.
        seccion_id (int):               Sección a la que pertenece el servidor.
        servicios  (list[int] | None):  IDs de servicios a asociar al servidor
                                        en el momento de creación. None = sin
                                        servicios iniciales.

    Campos excluidos deliberadamente:
        `hostname`, `pretty_os`, `arch`, `kernel` son descubiertos por el
        sondeo SSH posterior a la creación, no proporcionados por el usuario.
        `imagen` tiene su propio endpoint de subida.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    server_id: str = Field(alias="serverId")
    dns: str
    seccion_id: int = Field(alias="seccionId")
    servicios: Optional[list[int]] = None


class ServidorRead(SQLModel):
    """
    DTO de respuesta HTTP para la lectura de un servidor (GET).

    JSON en camelCase generado automáticamente por `alias_generator=to_camel`:
    `server_id` → `serverId`, `pretty_os` → `prettyOs`, `seccion_id` → `seccionId`,
    `imagen_url` → `imagenUrl`.

    Incluye todos los campos del ORM más dos campos calculados por la capa de
    servicio/repositorio antes de construir este esquema:

        imagen     (str | None):  Nombre del fichero en MinIO (campo interno
                                  del ORM, expuesto en la respuesta para que
                                  el cliente pueda construir la URL si lo necesita).
        imagen_url (str | None):  URL pública o firmada del fichero en MinIO.
                                  Calculada por `services/minio_service.py` a
                                  partir de `imagen`. None si el servidor no
                                  tiene imagen asignada.

        servicios  (list[int]):   IDs de los servicios asociados al servidor.
                                  Lista vacía `[]` por defecto (no None) para
                                  que el cliente no tenga que manejar un caso
                                  nulo al iterar.
    """

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
    """
    DTO interno para la actualización parcial de un servidor por el sondeo SSH.

    No se expone directamente como cuerpo de ningún endpoint HTTP. Lo usa
    `services/servidor_service.py` o el repositorio para propagar los campos
    descubiertos al conectarse al servidor por SSH: `hostname`, `pretty_os`,
    `arch`, `kernel`, así como campos de inventario que el sondeo puede también
    corregir (`server_id`, `dns`, `seccion_id`).

    Todos los campos son opcionales con `default=None` para el patrón PATCH:
    solo los campos no-None se escriben en la BD. El sondeo puede actualizar
    un subconjunto de campos sin afectar al resto.

    No usa `extra="forbid"` porque es uso interno; la seguridad se garantiza
    por el hecho de que el llamante es código propio de la aplicación.

    Aliases explícitos (`Field(alias=...)`) para los campos camelCase, por
    coherencia con la representación de la BD y con `ServidorCreate`.
    """

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
    """
    DTO público para la actualización parcial de un servidor por el usuario
    (PATCH /servidor/{id}).

    Expone solo los campos que el usuario puede modificar manualmente:
    datos de inventario que el administrador controla. Los campos de sistema
    descubiertos por SSH (`hostname`, `pretty_os`, `arch`, `kernel`) se
    excluyen deliberadamente: solo el sondeo SSH puede actualizarlos.

    `extra="forbid"` rechaza cualquier campo no declarado, previniendo que
    el usuario intente sobrescribir campos de sistema enviando campos no
    permitidos en la petición PATCH.

    Campos:
        server_id  (str | None):  Nuevo identificador externo. None = no actualizar.
        dns        (str | None):  Nueva dirección FQDN o IP. None = no actualizar.
        seccion_id (int | None):  Nueva sección. None = no actualizar.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    server_id: Optional[str] = Field(default=None, alias="serverId")
    dns: Optional[str] = None
    seccion_id: Optional[int] = Field(default=None, alias="seccionId")


class ServidorServicio(SQLModel, table=True):
    """
    Tabla de asociación muchos-a-muchos entre servidores y servicios.

    Representa qué servicios ejecuta cada servidor. Un servidor puede ejecutar
    múltiples servicios (web, base de datos, caché, etc.) y un mismo servicio
    puede ejecutarse en múltiples servidores.

    La clave primaria compuesta `(servidor_id, servicio_id)` garantiza que
    un servicio no puede asociarse más de una vez al mismo servidor.

    Las columnas en MariaDB usan nombres camelCase (`servidorId`, `servicioId`)
    por herencia del sistema Java anterior. El mapeo se realiza con
    `sa_column_kwargs={"name": "..."}`.

    Relaciones de clave foránea:
        servidor_id → servidores.id
        servicio_id → servicios.id
    """

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
