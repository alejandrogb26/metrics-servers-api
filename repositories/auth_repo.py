"""
Repositorio de ensamblado de datos de sesión para el flujo de autenticación.

Capa arquitectónica: Infraestructura / Persistencia relacional.

Responsabilidades:
    - Construir el objeto `SessionResponse` completo tras un login exitoso,
      consultando permisos del grupo y foto de perfil del usuario en una sola
      operación coordinada (`build_session`).
    - Aislar el acceso a datos necesario para la sesión del resto de la lógica
      de autenticación en `services/auth_service.py`.

Qué NO debe contener este fichero:
    - Verificación de credenciales LDAP. Eso pertenece a `services/ldap_service.py`.
    - Emisión del token JWT. Eso pertenece a `core/security.py` y se llama desde
      `services/auth_service.py`.
    - Resolución de URLs de MinIO. La transformación de `foto_perfil` (nombre de
      fichero) a `url_foto` (URL pública) se hace en `services/auth_service.py`
      después de que este repositorio devuelva el nombre del fichero.

Relaciones con otros módulos:
    - `models/grupo.py`            → `Grupo` ORM cuyo ID se usa para cargar
                                     los permisos del grupo del usuario.
    - `models/permission_map.py`   → `PermissionMap[str]` agrupa permisos globales
                                     y de sección como nombres legibles (strings),
                                     a diferencia de `PermissionMap[int]` (IDs)
                                     usado en otras operaciones CRUD.
    - `models/common.py`           → `SessionResponse` es el esquema de respuesta
                                     que este repositorio construye.
    - `models/usuario.py`          → `UsuarioApp` para obtener el nombre del
                                     fichero de foto de perfil del usuario.
    - `repositories/grupo_repo.py` → importado de forma diferida (lazy import)
                                     dentro de `build_session` para evitar
                                     importación circular a nivel de módulo.
    - `services/auth_service.py`   → llama a `build_session` y completa el
                                     `SessionResponse.url_foto` resolviendo la
                                     URL de MinIO con el `foto_perfil` devuelto.
    - `core/database.py`           → proporciona la `Session` inyectada en el
                                     constructor.

Autor:
    Alejandro Gómez Blanco

Proyecto:
    Metrics Servers

Versión:
    1.0.0

Organización:
    Metrics Servers Project
"""

from sqlmodel import Session, select
from models.grupo import Grupo
from models.permission_map import PermissionMap
from models.common import SessionResponse
from models.usuario import UsuarioApp


class AuthRepository:
    """
    Repositorio especializado en el ensamblado de datos de sesión.

    A diferencia de los repositorios de entidad simple (como `AmbitoRepository`),
    este repositorio no opera sobre una única tabla sino que coordina consultas
    sobre varias (`grupos`, `grupo_permiso_global`, `grupo_seccion`, `usuarios_app`)
    para construir la representación completa de la sesión del usuario autenticado.

    La sesión SQLAlchemy se recibe por constructor y no se gestiona aquí:
    commit, rollback y close son responsabilidad del llamante.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def build_session(
        self,
        username: str,
        display_name: str | None,
        mail: str | None,
        grupo: Grupo,
    ) -> tuple[SessionResponse, str | None]:
        """
        Construye el objeto `SessionResponse` con todos los datos de la sesión.

        Orquesta tres operaciones de lectura:
            1. Carga los nombres de permisos globales del grupo
               (`GrupoRepository.get_global_permission_names`).
            2. Carga los nombres de permisos por sección del grupo
               (`GrupoRepository.get_section_permission_names`).
            3. Obtiene el nombre del fichero de foto de perfil del usuario
               (`_get_foto_perfil`).

        Tipo de permiso `PermissionMap[str]`:
            A diferencia de `GrupoCreate`/`GrupoRead` que usan `PermissionMap[int]`
            (IDs de permisos), aquí se usa `PermissionMap[str]` porque el cliente
            recibe los nombres de los permisos (p. ej. `"ver_servidores"`) para
            que pueda evaluarlos sin necesidad de resolver los IDs contra otro
            endpoint.

        Serialización con `model_dump(by_alias=True)`:
            El `PermissionMap` se serializa a dict con aliases camelCase antes de
            asignarlo a `SessionResponse.permisos`. Esto produce `globalPerms`
            (en lugar de `global_perms`) en el JSON final, que es el formato que
            esperan los clientes Flutter y Swing.

        Responsabilidad de `url_foto`:
            El campo `SessionResponse.url_foto` no se rellena aquí. Este método
            devuelve el nombre del fichero (`foto_perfil`) como segundo elemento
            de la tupla para que `services/auth_service.py` resuelva la URL de
            MinIO y la asigne al `SessionResponse` antes de devolverlo al cliente.
            Separar esta responsabilidad evita que el repositorio dependa de
            `services/minio_service.py` (dependencia circular potencial).

        Importación diferida de `GrupoRepository`:
            Se importa dentro del cuerpo del método en lugar de en la cabecera del
            módulo para evitar una importación circular: `auth_repo` ← `grupo_repo`
            podría cerrar un ciclo si `grupo_repo` importara algo de `auth_repo`.

        Args:
            username:     Nombre de usuario (login) del usuario autenticado.
            display_name: Nombre visible del usuario obtenido de LDAP. Puede ser None.
            mail:         Correo electrónico del usuario desde LDAP. Puede ser None.
            grupo:        Objeto ORM `Grupo` del grupo al que pertenece el usuario.
                          Debe tener `id` asignado (cargado de la BD).

        Retorna:
            Tupla `(session_response, foto_perfil)` donde:
                - `session_response`: `SessionResponse` con todos los campos
                  excepto `url_foto`, que permanece None.
                - `foto_perfil`: nombre del fichero en MinIO, o None si el
                  usuario no tiene foto de perfil.
        """
        from repositories.grupo_repo import GrupoRepository
        repo = GrupoRepository(self.session)

        global_perms = repo.get_global_permission_names(grupo.id)  # type: ignore[arg-type]
        section_perms = repo.get_section_permission_names(grupo.id)  # type: ignore[arg-type]

        pmap = PermissionMap[str](
            global_perms=global_perms,
            sections=section_perms,
        )

        foto_perfil = self._get_foto_perfil(username)

        return SessionResponse(
            username=username,
            display_name=display_name,
            email=mail,
            grupo={
                "id": grupo.id,
                "nombre": grupo.nombre,
                "superadmin": grupo.superadmin,
            },
            # model_dump(by_alias=True) produce globalPerms en lugar de global_perms
            permisos=pmap.model_dump(by_alias=True),
            # url_foto se asigna en el service tras resolver la URL de MinIO
        ), foto_perfil

    def _get_foto_perfil(self, username: str) -> str | None:
        """
        Busca el nombre del fichero de foto de perfil del usuario en `usuarios_app`.

        Busca por `username` (no por `id`) porque en el contexto del login el único
        identificador disponible es el nombre de usuario recibido de LDAP, antes de
        que se conozca el `id` interno de la tabla `usuarios_app`.

        El campo `UsuarioApp.username` tiene un índice en BD (`index=True` en el
        modelo), por lo que esta búsqueda es eficiente incluso con muchos usuarios.

        Args:
            username: Nombre de usuario (login) a buscar en `usuarios_app`.

        Retorna:
            El nombre del fichero en MinIO (`foto_perfil`) si el usuario tiene
            registro en `usuarios_app` y foto asignada, o `None` en caso contrario
            (usuario sin registro en la tabla o sin foto de perfil).
        """
        usuario = self.session.exec(
            select(UsuarioApp).where(UsuarioApp.username == username)
        ).first()
        return usuario.foto_perfil if usuario else None
