"""
Modelos de dominio y esquemas de API para la entidad UsuarioApp.

Capa arquitectónica: Dominio / Modelos de datos.

Responsabilidades:
    - Definir la tabla `usuarios_app` en MariaDB mediante el modelo ORM
      `UsuarioApp`. Esta tabla almacena datos específicos de la aplicación
      para cada usuario: no duplica el directorio LDAP, sino que extiende
      el perfil del usuario con información propia de la API (foto de perfil,
      vínculo con el objeto Active Directory).
    - Definir el esquema de respuesta HTTP `UsuarioAppRead` para exponer los
      datos de perfil de usuario de forma controlada.

Qué NO debe contener este fichero:
    - Lógica de autenticación ni de sincronización con LDAP. Eso pertenece
      a `services/auth_service.py` y `services/ldap_service.py`.
    - Credenciales ni contraseñas. La API no almacena contraseñas; la
      autenticación es completamente delegada al directorio LDAP.
    - Datos de autorización (permisos, grupos). Esos pertenecen a
      `models/grupo.py`.

Relaciones con otros módulos:
    - `core/database.py`           → registra `UsuarioApp` en `SQLModel.metadata`.
    - `services/auth_service.py`   → crea o actualiza el registro `UsuarioApp`
                                     durante el login, sincronizando el `username`
                                     y el `ad_object_id` obtenidos de LDAP.
    - `services/minio_service.py`  → gestiona la subida de la foto de perfil y
                                     genera la `url_foto` incluida en `UsuarioAppRead`.
    - `core/dependencies.py`       → puede buscar al usuario por `username` para
                                     enriquecer el contexto de la petición.

Por qué existe `usuarios_app` si la autenticación es LDAP:
    Los usuarios se autentican contra LDAP, por lo que no es necesario almacenar
    credenciales en la BD. Sin embargo, la aplicación necesita persistir datos
    propios del usuario que no existen en el directorio LDAP:
        - La foto de perfil (`foto_perfil`): almacenada en MinIO, referenciada aquí.
        - El vínculo estable con el objeto de directorio (`ad_object_id`): el
          `username` puede cambiar (p. ej. por un cambio de nombre), pero el
          Object ID de Active Directory es inmutable y permite mantener la
          asociación incluso si el login del usuario cambia.

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

# Configuración Pydantic para los esquemas de respuesta HTTP.
# Serializa snake_case a camelCase en JSON (`foto_perfil` → `fotoPerfil`,
# `url_foto` → `urlFoto`) y permite instanciar con el nombre Python original.
_camel = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class UsuarioApp(SQLModel, table=True):
    """
    Modelo ORM que representa la tabla `usuarios_app` en MariaDB.

    No tiene clase base separada porque la entidad es simple y no comparte
    campos con otros modelos. Solo los campos propios de la aplicación se
    almacenan aquí; el resto del perfil del usuario (nombre completo, correo,
    grupo, permisos) se obtiene de LDAP en tiempo de autenticación y se
    incluye en el JWT, no en esta tabla.

    Campos:
        id           (int | None):   Clave primaria auto-incremental. `None`
                                     antes del primer commit.
        ad_object_id (str | None, max 100): Object ID del usuario en el
                                     directorio Active Directory / LDAP. Es
                                     un identificador inmutable del objeto de
                                     directorio (distinto del `username`, que
                                     puede cambiar si el login del usuario se
                                     modifica en AD). `None` si la integración
                                     con AD no está activa o si el atributo no
                                     está disponible en el directorio.
                                     Columna `adObjectId` en BD.
        username     (str, max 100): Nombre de usuario (login) tal como aparece
                                     en LDAP. Indexado (`index=True`) para
                                     búsquedas eficientes por login, que son
                                     la operación de acceso más frecuente sobre
                                     esta tabla.
        foto_perfil  (str | None, max 255): Nombre del fichero de foto de perfil
                                     almacenado en MinIO. `None` si el usuario
                                     no tiene foto de perfil. Columna `fotoPerfil`
                                     en BD. No se expone directamente al cliente:
                                     `UsuarioAppRead` ofrece `url_foto` en su lugar.
    """

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
    """
    Esquema de respuesta HTTP para los datos de perfil del usuario.

    Expone un subconjunto controlado de los campos de `UsuarioApp`: omite
    `ad_object_id` porque es un identificador interno del directorio que
    los clientes no necesitan conocer.

    Incluye `url_foto`, calculada por la capa de servicio a partir del nombre
    de fichero `foto_perfil` almacenado en el ORM. El patrón es el mismo que
    en `ServidorRead` (`imagen` + `imagen_url`): la respuesta expone tanto el
    nombre del fichero como la URL resuelta, permitiendo al cliente usar la
    URL directamente sin construirla.

    Campos:
        id         (int):         Identificador único del registro de app.
        username   (str):         Login del usuario.
        foto_perfil (str | None): Nombre del fichero en MinIO. Serializa como
                                  `fotoPerfil` en JSON.
        url_foto   (str | None):  URL pública o firmada de la foto de perfil
                                  en MinIO, generada por `services/minio_service.py`.
                                  `None` si no tiene foto asignada. Serializa
                                  como `urlFoto` en JSON.
    """

    model_config = _camel

    id: int
    username: str
    foto_perfil: Optional[str] = None   # JSON: fotoPerfil
    url_foto: Optional[str] = None      # JSON: urlFoto
