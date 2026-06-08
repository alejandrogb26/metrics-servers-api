"""
Repositorio de acceso a datos para la entidad Grupo y su modelo de permisos.

Capa arquitectónica: Infraestructura / Persistencia relacional.

Responsabilidades:
    - Encapsular todas las consultas y mutaciones SQL sobre las tablas `grupos`,
      `grupo_permiso_global` y `grupo_seccion`.
    - Proveer operaciones CRUD completas sobre grupos (incluyendo borrado masivo).
    - Gestionar el modelo de permisos de dos niveles: reemplazar, parcheado
      incremental y lectura de permisos globales y de sección.
    - Cargar permisos de forma eficiente en los listados (batch IN-query para
      evitar el problema N+1).

Qué NO debe contener este fichero:
    - Lógica de negocio ni validaciones de dominio.
    - Gestión del ciclo de vida de la sesión fuera de los métodos de escritura.
      Los métodos de lectura no hacen commit/rollback; los de escritura sí, de
      forma explícita con try/except.
    - Construcción de respuestas HTTP ni lógica de presentación.

Relaciones con otros módulos:
    - `models/grupo.py`            → `Grupo`, `GrupoCreate`, `GrupoPatch`,
                                     `GrupoPermisoGlobal`, `GrupoSeccion`,
                                     `GrupoRead` son los modelos que usa este repo.
    - `models/permiso.py`          → `Permiso` se une en las queries de nombres
                                     de permisos (global y de sección).
    - `models/ambito.py`           → `Ambito` se une para construir el nombre
                                     compuesto `permiso_ambito`.
    - `models/permission_map.py`   → `PermissionMap[int]` y `PermissionMap[str]`
                                     son los tipos de retorno de las operaciones
                                     de lectura de permisos.
    - `core/database.py`           → proporciona la `Session` inyectada.
    - `repositories/auth_repo.py`  → importa este repositorio de forma diferida
                                     para construir la respuesta de sesión.
    - `core/dependencies.py`       → llama a `get_global_permission_ids` y
                                     `get_section_permission_ids` para la
                                     autorización por petición.

Formato de nombre de permiso:
    Los métodos `get_*_permission_names` devuelven strings con el formato
    `"{permiso.nombre}_{ambito.nombre}"` (p. ej. `"ver_servidores"`).
    Este es el formato que `core/dependencies.py` compara contra los permisos
    requeridos por cada endpoint. Cambiar este formato rompería la autorización.

Autor:
    Alejandro Gómez Blanco

Proyecto:
    Metrics Servers

Versión:
    1.0.0

Organización:
    Metrics Servers Project
"""

from sqlalchemy import delete, func, insert
from sqlmodel import Session, select

from models.ambito import Ambito
from models.grupo import Grupo, GrupoCreate, GrupoPatch, GrupoPermisoGlobal, GrupoRead, GrupoSeccion
from models.permiso import Permiso
from models.permission_map import PermissionMap


class GrupoRepository:
    """
    Repositorio para las tablas `grupos`, `grupo_permiso_global` y `grupo_seccion`.

    Gestiona tanto el CRUD básico de grupos como el modelo completo de permisos
    de dos niveles. Los métodos de escritura gestionan su propio commit/rollback;
    los de lectura son de solo lectura y no tocan la transacción.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    # ── Lecturas ───────────────────────────────────────────────────────────────

    def find_by_id(self, grupo_id: int) -> GrupoRead | None:
        """
        Busca un grupo por PK y lo devuelve con sus permisos cargados.

        Usa `Session.get()` para aprovechar el identity map de SQLAlchemy antes
        de emitir una query a la BD. Si el grupo existe, carga sus permisos con
        dos queries adicionales (global + sección) vía `_to_read`. Para un único
        grupo este coste es fijo y aceptable.

        Args:
            grupo_id: Clave primaria del grupo a buscar.

        Retorna:
            `GrupoRead` con permisos cargados, o `None` si no existe.
        """
        grupo = self.session.get(Grupo, grupo_id)
        if grupo is None:
            return None
        return self._to_read(grupo)

    def find_all(self, offset: int, limit: int) -> tuple[list[GrupoRead], int]:
        """
        Devuelve una página de grupos con sus permisos y el total de registros.

        Optimización para evitar el problema N+1:
            En lugar de cargar los permisos de cada grupo con dos queries
            individuales (lo que produciría 2·N queries adicionales), se cargan
            todos los permisos de los grupos de la página en dos queries batch
            con `WHERE grupo_id IN (...)` mediante los helpers
            `_get_all_global_perm_ids` y `_get_all_section_perm_ids`. Los
            resultados se organizan en dicts Python indexados por `grupo_id`
            para la asignación O(1) a cada `GrupoRead`.

        Condición de carrera en la paginación:
            El `COUNT(*)` y el `SELECT LIMIT/OFFSET` son queries separadas.
            Un INSERT o DELETE concurrente entre ambas puede producir una
            inconsistencia de ±1 en `total`. Aceptable para paginación de
            catálogos con baja tasa de escritura.

        Args:
            offset: Registros a saltar (= page * size).
            limit:  Máximo de registros a devolver (= size).

        Retorna:
            Tupla `(grupos_con_permisos, total_sin_paginar)`.
        """
        # COUNT(*) y SELECT LIMIT/OFFSET son dos queries separadas: un INSERT/DELETE
        # concurrente entre ellas puede hacer que `total` no coincida exactamente con
        # len(items). Trade-off aceptable para paginación de lectura sin escritura muy alta.
        total: int = self.session.exec(select(func.count()).select_from(Grupo)).one()
        grupos = list(self.session.exec(select(Grupo).offset(offset).limit(limit)).all())
        if not grupos:
            return [], total

        grupo_ids = [g.id for g in grupos]
        global_map = self._get_all_global_perm_ids(grupo_ids)  # type: ignore[arg-type]
        section_map = self._get_all_section_perm_ids(grupo_ids)  # type: ignore[arg-type]

        result = [
            GrupoRead(
                id=g.id,
                nombre=g.nombre,
                dn=g.dn,
                superadmin=g.superadmin,
                permisos=PermissionMap[int](
                    global_perms=global_map.get(g.id, []),  # type: ignore[arg-type]
                    sections=section_map.get(g.id, {}),  # type: ignore[arg-type]
                ),
            )
            for g in grupos
        ]
        return result, total

    def find_by_any_dn(self, dns: list[str]) -> Grupo | None:
        """
        Busca el grupo de la aplicación cuyo DN coincida con alguno de la lista.

        Se usa durante el login LDAP: el usuario pertenece a uno o varios grupos
        del directorio (cuyos DNs se obtienen de LDAP). Este método determina
        cuál de esos grupos tiene un registro en la tabla `grupos` de la app.

        Restricción de unicidad:
            Si el usuario pertenece a más de un grupo registrado en la app,
            lanza `ValueError`. La aplicación asume que cada usuario tiene un
            único grupo de acceso. Si dos grupos del directorio están registrados
            en la app y el usuario pertenece a ambos, la situación es ambigua y
            debe resolverse en la configuración del directorio.

        Args:
            dns: Lista de Distinguished Names de los grupos LDAP del usuario.
                 Si la lista está vacía, retorna `None` inmediatamente.

        Retorna:
            El objeto `Grupo` ORM coincidente, o `None` si ningún DN de la lista
            corresponde a un grupo registrado en la aplicación.

        Lanza:
            ValueError: Si más de un grupo de la app coincide con los DNs del usuario.
        """
        if not dns:
            return None
        stmt = select(Grupo).where(Grupo.dn.in_(dns))  # type: ignore[union-attr]
        results = list(self.session.exec(stmt).all())
        if not results:
            return None
        if len(results) > 1:
            raise ValueError("El usuario pertenece a más de un grupo válido de la aplicación")
        return results[0]

    # ── Escritura ──────────────────────────────────────────────────────────────

    def insert(self, data: GrupoCreate) -> int:
        """
        Inserta un nuevo grupo con sus permisos iniciales de forma atómica.

        Flujo de la operación:
            1. Crea el objeto `Grupo` ORM y lo añade a la sesión.
            2. Llama a `session.flush()` para enviar el INSERT a la BD sin hacer
               commit. El flush es necesario para que la BD asigne el `id`
               auto-incremental antes de continuar.
            3. Si `data.permisos` está definido, inserta los permisos globales y
               de sección usando el `id` recién obtenido.
            4. Hace commit de toda la operación como una sola transacción.

        En caso de cualquier excepción hace rollback explícito y re-lanza el
        error para que el llamante (servicio o router) pueda manejarlo.

        Args:
            data: DTO `GrupoCreate` con nombre, DN, superadmin y permisos opcionales.

        Retorna:
            ID entero del grupo recién creado.
        """
        try:
            grupo = Grupo(
                nombre=data.nombre,
                dn=data.dn,
                superadmin=data.superadmin or False,
            )
            self.session.add(grupo)
            self.session.flush()
            assert grupo.id is not None         # Verificamos que después del 'flush()' la bd ha generado el id del grupo.
                                                # Si sigue siendo None, Python lanzará un AssertionError. 
            if data.permisos:
                self._save_permissions(grupo.id, data.permisos)

            self.session.commit()
            return grupo.id
        except Exception:
            self.session.rollback()
            raise

    def update(self, grupo_id: int, patch: GrupoPatch) -> bool:
        """
        Actualiza nombre y/o DN de un grupo existente (PATCH semántico).

        Aplica solo los campos presentes en la petición original, distinguiendo
        entre "campo ausente" y "campo presente con valor None":

            `nombre`:
                Solo se actualiza si `patch.nombre is not None`. No hay caso
                de uso para borrar el nombre (es obligatorio en el modelo).

            `dn`:
                Se comprueba con `"dn" in patch.model_fields_set` en lugar de
                `patch.dn is not None`. Esto permite enviar `"dn": null` para
                borrar el DN explícitamente (desvincula el grupo del directorio
                LDAP). Si `dn` no aparece en la petición, `model_fields_set`
                no lo contiene y el campo no se toca.

        `model_fields_set` es el conjunto de campos que el cliente incluyó en
        la petición JSON. Es la única forma de distinguir "no enviado" de
        "enviado como null" en Pydantic v2.

        Args:
            grupo_id: ID del grupo a actualizar.
            patch:    DTO `GrupoPatch` con los campos a modificar.

        Retorna:
            True si el grupo existía y se actualizó; False si no existe.
        """
        grupo = self.session.get(Grupo, grupo_id)
        if grupo is None:
            return False
        try:
            if patch.nombre is not None:
                grupo.nombre = patch.nombre
            if "dn" in patch.model_fields_set:
                grupo.dn = patch.dn  # None borra el DN
            self.session.add(grupo)
            self.session.commit()
            return True
        except Exception:
            self.session.rollback()
            raise

    def update_superadmin(self, grupo_id: int, superadmin: bool) -> bool:
        """
        Actualiza exclusivamente el flag `superadmin` de un grupo.

        Operación separada de `update` por diseño de seguridad: elevar o retirar
        privilegios de superadmin es una acción de alto impacto que dispone de
        su propio endpoint (`SuperAdminPatch`) con controles de autorización
        independientes.

        Args:
            grupo_id:   ID del grupo a modificar.
            superadmin: Nuevo valor del flag.

        Retorna:
            True si el grupo existía y se actualizó; False si no existe.
        """
        grupo = self.session.get(Grupo, grupo_id)
        if grupo is None:
            return False
        try:
            grupo.superadmin = superadmin
            self.session.add(grupo)
            self.session.commit()
            return True
        except Exception:
            self.session.rollback()
            raise

    def delete(self, grupo_id: int) -> bool:
        """
        Elimina un grupo por PK.

        Usa `session.delete()` (ORM-level delete) en lugar de un DELETE SQL
        directo. Esto activa los event listeners de SQLAlchemy y, si el ORM
        está configurado con cascadas, propaga el borrado a entidades relacionadas.

        Args:
            grupo_id: ID del grupo a eliminar.

        Retorna:
            True si el grupo existía y se eliminó; False si no existe.
        """
        grupo = self.session.get(Grupo, grupo_id)
        if grupo is None:
            return False
        try:
            self.session.delete(grupo)
            self.session.commit()
            return True
        except Exception:
            self.session.rollback()
            raise

    def delete_bulk(self, ids: list[int]) -> int:
        """
        Elimina múltiples grupos en una única sentencia DELETE.

        Más eficiente que llamar a `delete()` en bucle porque emite un solo
        `DELETE FROM grupos WHERE id IN (...)` en lugar de N queries
        individuales.

        Args:
            ids: Lista de IDs a eliminar. Si la lista está vacía, retorna 0
                 sin emitir ninguna query.

        Retorna:
            Número de filas eliminadas realmente (`rowcount`). Puede ser menor
            que `len(ids)` si algunos IDs no existían.
        """
        if not ids:
            return 0
        try:
            result = self.session.execute(delete(Grupo).where(Grupo.id.in_(ids)))
            self.session.commit()
            return result.rowcount  # type: ignore[return-value]
        except Exception:
            self.session.rollback()
            raise

    # ── Permisos ───────────────────────────────────────────────────────────────

    def get_global_permission_names(self, grupo_id: int) -> list[str]:
        """
        Devuelve los nombres compuestos de los permisos globales del grupo.

        Realiza un JOIN entre `grupo_permiso_global`, `permisos` y `ambitos`
        para obtener el nombre del permiso y el nombre de su ámbito. El
        nombre compuesto resultante sigue el formato `"{permiso}_{ambito}"`
        (p. ej. `"ver_servidores"`), que es el formato que `core/dependencies.py`
        compara contra los permisos requeridos por cada endpoint.

        Se usa en `repositories/auth_repo.py` para construir el `PermissionMap[str]`
        del `SessionResponse` (los clientes reciben nombres, no IDs).

        Args:
            grupo_id: ID del grupo cuyos permisos globales se quieren leer.

        Retorna:
            Lista de strings `"{permiso.nombre}_{ambito.nombre}"`. Lista vacía
            si el grupo no tiene permisos globales.
        """
        stmt = (
            select(Permiso.nombre, Ambito.nombre)
            .join(GrupoPermisoGlobal, GrupoPermisoGlobal.permiso_id == Permiso.id)
            .join(Ambito, Ambito.id == Permiso.ambito_id)
            .where(GrupoPermisoGlobal.grupo_id == grupo_id)
        )
        rows = self.session.exec(stmt).all()  # type: ignore[call-overload]
        return [f"{row[0]}_{row[1]}" for row in rows]

    def get_section_permission_names(self, grupo_id: int) -> dict[int, list[str]]:
        """
        Devuelve los nombres compuestos de los permisos de sección del grupo,
        organizados por `seccion_id`.

        Mismo formato de nombre compuesto que `get_global_permission_names`:
        `"{permiso.nombre}_{ambito.nombre}"`. El resultado es un dict donde la
        clave es el `seccion_id` y el valor es la lista de nombres de permisos
        que el grupo tiene en esa sección.

        Se usa en `repositories/auth_repo.py` para el `SessionResponse`.

        Args:
            grupo_id: ID del grupo cuyos permisos de sección se quieren leer.

        Retorna:
            Dict `{seccion_id: [nombre_permiso, ...]}`. Dict vacío si el grupo
            no tiene permisos de sección.
        """
        stmt = (
            select(GrupoSeccion.seccion_id, Permiso.nombre, Ambito.nombre)
            .join(Permiso, Permiso.id == GrupoSeccion.permiso_id)
            .join(Ambito, Ambito.id == Permiso.ambito_id)
            .where(GrupoSeccion.grupo_id == grupo_id)
        )
        rows = self.session.exec(stmt).all()  # type: ignore[call-overload]
        result: dict[int, list[str]] = {}
        for row in rows:
            result.setdefault(row[0], []).append(f"{row[1]}_{row[2]}")
        return result

    def get_global_permission_ids(self, grupo_id: int) -> list[int]:
        """
        Devuelve los IDs de los permisos globales del grupo.

        Versión ligera de `get_global_permission_names`: no hace JOIN con
        `permisos` ni `ambitos`, solo lee `grupo_permiso_global`. Se usa en
        `core/dependencies.py` para la comprobación de autorización por petición
        y en `_to_read` para construir `GrupoRead` con `PermissionMap[int]`.

        Args:
            grupo_id: ID del grupo.

        Retorna:
            Lista de IDs de permisos globales. Lista vacía si no tiene.
        """
        rows = self.session.exec(
            select(GrupoPermisoGlobal).where(GrupoPermisoGlobal.grupo_id == grupo_id)
        ).all()
        return [r.permiso_id for r in rows]

    def get_section_permission_ids(self, grupo_id: int) -> dict[int, list[int]]:
        """
        Devuelve los IDs de los permisos de sección del grupo, por sección.

        Versión ligera de `get_section_permission_names`: solo lee `grupo_seccion`
        sin JOIN adicionales. Se usa en `core/dependencies.py` para autorización
        y en `_to_read` para `GrupoRead` con `PermissionMap[int]`.

        Args:
            grupo_id: ID del grupo.

        Retorna:
            Dict `{seccion_id: [permiso_id, ...]}`. Dict vacío si no tiene
            permisos de sección.
        """
        rows = self.session.exec(
            select(GrupoSeccion).where(GrupoSeccion.grupo_id == grupo_id)
        ).all()
        result: dict[int, list[int]] = {}
        for row in rows:
            result.setdefault(row.seccion_id, []).append(row.permiso_id)
        return result

    def replace_all_permissions(self, grupo_id: int, permisos: PermissionMap[int]) -> bool:
        """
        Reemplaza la totalidad de permisos del grupo (globales y de sección).

        Operación destructiva: elimina todos los permisos existentes del grupo
        antes de insertar los nuevos. Se ejecuta como una única transacción para
        garantizar que el grupo nunca quede sin permisos de forma visible durante
        la operación (no hay ventana entre el DELETE y el INSERT).

        Útil cuando el cliente envía el estado deseado completo de los permisos
        en lugar de una diferencia incremental.

        Args:
            grupo_id: ID del grupo.
            permisos: `PermissionMap[int]` con el nuevo conjunto completo de
                      permisos globales y de sección.

        Retorna:
            True si el grupo existe y se actualizó; False si no existe.
        """
        if not self.session.get(Grupo, grupo_id):
            return False
        try:
            self._delete_all_permissions(grupo_id)
            self._save_permissions(grupo_id, permisos)
            self.session.commit()
            return True
        except Exception:
            self.session.rollback()
            raise

    def patch_global_permissions(
        self, grupo_id: int, to_add: list[int] | None, to_remove: list[int] | None
    ) -> bool:
        """
        Añade y/o elimina permisos globales de un grupo de forma incremental.

        A diferencia de `replace_all_permissions`, no toca los permisos que no
        están en ninguna de las dos listas. Permite operaciones delta: añadir
        solo algunos permisos, quitar solo otros, o ambas cosas en una misma
        petición atómica.

        El INSERT usa `INSERT IGNORE` (`.prefix_with("IGNORE")`) para ignorar
        silenciosamente los IDs de `to_add` que ya existían en la tabla, en lugar
        de lanzar un error de clave duplicada. Esto hace la operación idempotente.

        Args:
            grupo_id:  ID del grupo a modificar.
            to_add:    Lista de IDs de permisos a añadir. None = no añadir nada.
            to_remove: Lista de IDs de permisos a eliminar. None = no eliminar nada.

        Retorna:
            True si el grupo existe y la operación se completó; False si no existe.
        """
        if not self.session.get(Grupo, grupo_id):
            return False
        try:
            if to_remove:
                self.session.execute(
                    delete(GrupoPermisoGlobal).where(
                        GrupoPermisoGlobal.grupo_id == grupo_id,
                        GrupoPermisoGlobal.permiso_id.in_(to_remove),
                    )
                )
            if to_add:
                self.session.execute(
                    insert(GrupoPermisoGlobal).prefix_with("IGNORE").values(
                        [{"grupo_id": grupo_id, "permiso_id": pid} for pid in to_add]
                    )
                )
            self.session.commit()
            return True
        except Exception:
            self.session.rollback()
            raise

    def replace_section_permissions(
        self, grupo_id: int, seccion_id: int, permiso_ids: list[int]
    ) -> bool:
        """
        Reemplaza todos los permisos de una sección concreta para un grupo.

        Elimina todas las filas de `grupo_seccion` para `(grupo_id, seccion_id)`
        y las reemplaza con `permiso_ids`. Solo afecta a esa combinación
        grupo-sección; los permisos de otras secciones del mismo grupo no se tocan.

        Si `permiso_ids` está vacío, el resultado es que el grupo queda sin
        permisos en esa sección (solo se ejecuta el DELETE, no el INSERT).

        Args:
            grupo_id:    ID del grupo.
            seccion_id:  ID de la sección cuyos permisos se reemplazan.
            permiso_ids: Nueva lista de IDs de permisos para esa sección.

        Retorna:
            True si el grupo existe y la operación se completó; False si no existe.
        """
        if not self.session.get(Grupo, grupo_id):
            return False
        try:
            self.session.execute(
                delete(GrupoSeccion).where(
                    GrupoSeccion.grupo_id == grupo_id,
                    GrupoSeccion.seccion_id == seccion_id,
                )
            )
            if permiso_ids:
                self.session.execute(
                    insert(GrupoSeccion).prefix_with("IGNORE").values(
                        [
                            {"grupo_id": grupo_id, "seccion_id": seccion_id, "permiso_id": pid}
                            for pid in permiso_ids
                        ]
                    )
                )
            self.session.commit()
            return True
        except Exception:
            self.session.rollback()
            raise

    def patch_section_permissions(
        self, grupo_id: int, seccion_id: int, to_add: list[int] | None, to_remove: list[int] | None
    ) -> bool:
        """
        Añade y/o elimina permisos de una sección concreta de forma incremental.

        Equivalente a `patch_global_permissions` pero acotado a una sección.
        Solo modifica las filas de `grupo_seccion` que corresponden a
        `(grupo_id, seccion_id, permiso_id IN to_remove/to_add)`.

        El INSERT usa `INSERT IGNORE` para idempotencia: añadir un permiso que
        ya existe no genera error.

        Args:
            grupo_id:   ID del grupo.
            seccion_id: ID de la sección.
            to_add:     IDs de permisos a añadir en esa sección. None = nada.
            to_remove:  IDs de permisos a eliminar de esa sección. None = nada.

        Retorna:
            True si el grupo existe y la operación se completó; False si no existe.
        """
        if not self.session.get(Grupo, grupo_id):
            return False
        try:
            if to_remove:
                self.session.execute(
                    delete(GrupoSeccion).where(
                        GrupoSeccion.grupo_id == grupo_id,
                        GrupoSeccion.seccion_id == seccion_id,
                        GrupoSeccion.permiso_id.in_(to_remove),
                    )
                )
            if to_add:
                self.session.execute(
                    insert(GrupoSeccion).prefix_with("IGNORE").values(
                        [
                            {"grupo_id": grupo_id, "seccion_id": seccion_id, "permiso_id": pid}
                            for pid in to_add
                        ]
                    )
                )
            self.session.commit()
            return True
        except Exception:
            self.session.rollback()
            raise

    # ── Helpers privados ───────────────────────────────────────────────────────

    def _to_read(self, grupo: Grupo) -> GrupoRead:
        """
        Convierte un objeto ORM `Grupo` a `GrupoRead` cargando sus permisos.

        Llama a `get_global_permission_ids` y `get_section_permission_ids`
        individualmente (dos queries). Solo se usa desde `find_by_id`, donde
        el número de grupos es siempre 1. Para listados usa `find_all`, que
        aplica la estrategia batch para evitar N+1.
        """
        pmap = PermissionMap[int](
            global_perms=self.get_global_permission_ids(grupo.id),  # type: ignore[arg-type]
            sections=self.get_section_permission_ids(grupo.id),  # type: ignore[arg-type]
        )
        return GrupoRead(
            id=grupo.id,  # type: ignore[arg-type]
            nombre=grupo.nombre,
            dn=grupo.dn,
            superadmin=grupo.superadmin,
            permisos=pmap,
        )

    def _delete_all_permissions(self, grupo_id: int) -> None:
        """
        Elimina todos los permisos del grupo (globales y de sección).

        Emite dos DELETE separados: uno sobre `grupo_permiso_global` y otro
        sobre `grupo_seccion`. No hace commit; el llamante es responsable de
        la transacción. Se llama siempre dentro de un try/except con rollback.
        """
        self.session.execute(
            delete(GrupoPermisoGlobal).where(GrupoPermisoGlobal.grupo_id == grupo_id)
        )
        self.session.execute(
            delete(GrupoSeccion).where(GrupoSeccion.grupo_id == grupo_id)
        )

    def _save_permissions(self, grupo_id: int, pmap: PermissionMap[int]) -> None:
        """
        Inserta los permisos de un `PermissionMap[int]` para un grupo.

        Usa `INSERT IGNORE` en ambas tablas para manejar duplicados sin error.
        No hace commit; el llamante gestiona la transacción.

        Para `sections`, los IDs de sección se convierten explícitamente con
        `int(sid)` porque las claves de un dict deserializado desde JSON son
        siempre strings; sin la conversión, la inserción SQL fallaría por tipo
        de dato incorrecto.
        """
        if pmap.global_perms:
            self.session.execute(
                insert(GrupoPermisoGlobal).prefix_with("IGNORE").values(
                    [{"grupo_id": grupo_id, "permiso_id": pid} for pid in pmap.global_perms]
                )
            )
        if pmap.sections:
            rows = [
                {"grupo_id": grupo_id, "seccion_id": int(sid), "permiso_id": pid}
                for sid, pids in pmap.sections.items()
                for pid in pids
            ]
            if rows:
                self.session.execute(
                    insert(GrupoSeccion).prefix_with("IGNORE").values(rows)
                )

    def _get_all_global_perm_ids(self, grupo_ids: list[int]) -> dict[int, list[int]]:
        """
        Carga en una sola query los permisos globales de todos los grupos indicados.

        Usa `WHERE grupo_id IN (...)` para obtener todas las filas relevantes de
        `grupo_permiso_global` de una vez. El resultado se organiza en un dict
        `{grupo_id: [permiso_id, ...]}` para acceso O(1) desde `find_all`.

        Args:
            grupo_ids: Lista de IDs de grupos a consultar. Lista vacía → dict vacío.
        """
        if not grupo_ids:
            return {}
        rows = self.session.exec(
            select(GrupoPermisoGlobal).where(GrupoPermisoGlobal.grupo_id.in_(grupo_ids))
        ).all()
        result: dict[int, list[int]] = {}
        for row in rows:
            result.setdefault(row.grupo_id, []).append(row.permiso_id)
        return result

    def _get_all_section_perm_ids(self, grupo_ids: list[int]) -> dict[int, dict[int, list[int]]]:
        """
        Carga en una sola query los permisos de sección de todos los grupos indicados.

        Estructura del resultado: `{grupo_id: {seccion_id: [permiso_id, ...]}}`.
        Permite asignación O(1) en `find_all` tanto por grupo como por sección,
        sin queries adicionales.

        Args:
            grupo_ids: Lista de IDs de grupos a consultar. Lista vacía → dict vacío.
        """
        if not grupo_ids:
            return {}
        rows = self.session.exec(
            select(GrupoSeccion).where(GrupoSeccion.grupo_id.in_(grupo_ids))
        ).all()
        result: dict[int, dict[int, list[int]]] = {}
        for row in rows:
            result.setdefault(row.grupo_id, {}).setdefault(row.seccion_id, []).append(row.permiso_id)
        return result
