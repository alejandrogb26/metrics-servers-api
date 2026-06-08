"""
Repositorio de acceso a datos para la entidad UsuarioApp.

Capa arquitectónica: Infraestructura / Persistencia relacional.

Responsabilidades:
    - Buscar registros en la tabla `usuarios_app` por los dos identificadores
      únicos disponibles: `username` (mutable) y `ad_object_id` (inmutable).
    - Crear nuevos registros de usuario la primera vez que un usuario LDAP inicia
      sesión en la aplicación (`insert`).
    - Actualizar el nombre del fichero de foto de perfil tras subirlo a MinIO
      (`update_foto`).

Qué NO debe contener este fichero:
    - Autenticación ni validación LDAP. Eso pertenece a `services/ldap_service.py`.
    - Subida ni gestión de ficheros en MinIO. Eso pertenece a
      `services/minio_service.py`.
    - Generación de `url_foto`. El repositorio almacena y devuelve el nombre del
      fichero (`foto_perfil`); la transformación a URL la hace la capa de servicio.
    - Paginación ni listado de usuarios. Los usuarios se gestionan a través de
      LDAP/AD; este repositorio solo persiste los datos locales de la aplicación.
    - Eliminación de usuarios. El ciclo de vida de las cuentas de usuario está
      gobernado por Active Directory.

Relaciones con otros módulos:
    - `models/usuario.py`         → `UsuarioApp` (ORM) y `UsuarioAppRead` (respuesta).
    - `core/database.py`          → proporciona la `Session` inyectada en el constructor.
    - `repositories/auth_repo.py` → llama a `find_by_username` en el flujo de login
                                    para localizar el registro de foto de perfil.
    - `services/auth_service.py`  → llama a `find_by_ad_object_id` para detectar si
                                    el usuario ya existe y a `insert` para crearlo en el
                                    primer login.
    - `services/minio_service.py` → llama a `update_foto` tras subir el fichero para
                                    persistir el nombre resultante en la BD.

Diseño reducido intencional:
    Este repositorio no incluye `find_all` ni `delete` porque el inventario de
    usuarios es exclusivo de LDAP/AD. La tabla `usuarios_app` solo existe para
    persistir datos propios de la aplicación (foto de perfil, ObjectID de AD) que
    no existen en el directorio corporativo.

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
from models.usuario import UsuarioApp


class UsuarioRepository:
    """
    Repositorio de lectura/escritura para la tabla `usuarios_app`.

    Opera sobre los datos locales de aplicación de cada usuario: el campo
    `ad_object_id` (inmutable, identificador permanente de AD) y `foto_perfil`
    (nombre del fichero de foto en MinIO). No gestiona credenciales, contraseñas
    ni atributos LDAP.

    Los métodos de escritura gestionan su propio commit/rollback.
    Los de lectura son de solo lectura y no tocan la transacción.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def find_by_username(self, username: str) -> UsuarioApp | None:
        """
        Busca un usuario por su nombre de cuenta (campo `username`).

        Usa `select().where()` porque `username` no es la clave primaria del
        modelo; `session.get()` solo funciona con PK. El campo `username` está
        declarado con índice en el modelo para que esta búsqueda sea eficiente.

        Se usa principalmente en el flujo de login (`auth_repo.build_session`)
        para recuperar el nombre del fichero de foto de perfil, dado que en ese
        punto solo se dispone del `username` del usuario autenticado.

        Args:
            username: Nombre de cuenta del usuario (ej. "jsmith"). Distingue
                      mayúsculas/minúsculas según la collation de la columna en BD.

        Retorna:
            Objeto ORM `UsuarioApp` si existe un registro con ese `username`,
            `None` si el usuario no tiene todavía un registro local en la
            aplicación (puede ocurrir si el usuario nunca ha iniciado sesión).
        """
        stmt = select(UsuarioApp).where(UsuarioApp.username == username)
        return self.session.exec(stmt).first()

    def find_by_ad_object_id(self, ad_object_id: str) -> UsuarioApp | None:
        """
        Busca un usuario por su ObjectID de Active Directory (campo `ad_object_id`).

        El `ad_object_id` es el identificador permanente e inmutable que AD asigna
        a cada objeto de directorio. A diferencia de `username` (que puede cambiar
        si el usuario cambia su nombre de cuenta), el `ad_object_id` nunca varía,
        lo que lo hace ideal como clave de vinculación entre el directorio y la BD
        local de la aplicación.

        Usa `select().where()` porque `ad_object_id` no es la PK del modelo.
        Se usa en el flujo de login para comprobar si el usuario ya tiene un
        registro local antes de crearlo con `insert`.

        Args:
            ad_object_id: ObjectID de Active Directory (GUID en formato string,
                          ej. "S-1-5-21-..."). Inmutable para la vida del objeto AD.

        Retorna:
            Objeto ORM `UsuarioApp` si existe un registro con ese `ad_object_id`,
            `None` si el usuario aún no tiene registro local (primer login).
        """
        stmt = select(UsuarioApp).where(UsuarioApp.ad_object_id == ad_object_id)
        return self.session.exec(stmt).first()

    def insert(self, usuario: UsuarioApp) -> UsuarioApp:
        """
        Persiste un nuevo registro de usuario en la tabla `usuarios_app`.

        A diferencia de otros repositorios del proyecto, este método no recibe
        un DTO de creación (`XxxCreate`) y construye el objeto ORM internamente.
        En su lugar, recibe un objeto `UsuarioApp` ya construido por la capa de
        servicio. Esto permite al servicio combinar los datos de LDAP con los
        campos propios de la aplicación antes de persistir el registro.

        Llama a `session.refresh(usuario)` tras el commit para garantizar que el
        `id` auto-incremental asignado por MariaDB esté disponible en el objeto
        devuelto. Sin el `refresh`, SQLAlchemy puede expirar los atributos al
        hacer commit, dejando el objeto en estado parcial.

        Args:
            usuario: Objeto `UsuarioApp` ORM completamente inicializado por el
                     llamante (con `username`, `ad_object_id` y opcionalmente
                     `foto_perfil`).

        Retorna:
            El mismo objeto `UsuarioApp` recargado desde la BD, con `id` asignado.

        Lanza:
            Cualquier excepción de SQLAlchemy (ej. `IntegrityError` si ya existe
            un registro con el mismo `username` o `ad_object_id`). El rollback
            se gestiona internamente antes de re-lanzar.
        """
        try:
            self.session.add(usuario)
            self.session.commit()
            self.session.refresh(usuario)
            return usuario
        except Exception:
            self.session.rollback()
            raise

    def update_foto(self, username: str, nombre_archivo: str) -> None:
        """
        Actualiza el nombre del fichero de foto de perfil del usuario en la BD.

        Método dedicado para la gestión de la foto de perfil, análogo a
        `ServicioRepository.update_logo` y `ServidorRepository.update_imagen`.
        Se llama desde el servicio de MinIO tras subir el fichero, para persistir
        el nombre del fichero resultante en la columna `foto_perfil`.

        Internamente usa `find_by_username` para localizar el registro. Esto
        emite una query adicional de lectura antes de la escritura (dos queries
        en total: SELECT + UPDATE), a diferencia de `session.get()` que
        aprovecharía el identity map si el objeto ya estuviera cargado.

        Solo actualiza el campo `foto_perfil` (nombre del fichero en MinIO). La
        generación de la URL pública a partir de ese nombre es responsabilidad
        de la capa de servicio al construir `UsuarioAppRead`.

        Comportamiento si el usuario no existe:
            Retorna `None` silenciosamente sin modificar nada. A diferencia de
            otros métodos del proyecto que devuelven `False` para señalar la
            ausencia (ej. `update` y `delete` en repositorios de Seccion o
            Servicio), este método no comunica al llamante si el usuario existía
            o no. El llamante debe verificar la existencia del usuario antes de
            llamar a este método si necesita garantizar que la actualización se
            realizó.

        Args:
            username:       Nombre de cuenta del usuario cuya foto se actualiza.
            nombre_archivo: Nombre del fichero tal como quedó almacenado en el
                            bucket de MinIO.
        """
        usuario = self.find_by_username(username)
        if usuario is None:
            return
        try:
            usuario.foto_perfil = nombre_archivo
            self.session.add(usuario)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
