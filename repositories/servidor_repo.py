"""
Repositorio de acceso a datos para la entidad Servidor y sus asociaciones.

Capa arquitectónica: Infraestructura / Persistencia relacional.

Responsabilidades:
    - Encapsular todas las consultas y mutaciones SQL sobre la tabla `servidores`
      y la tabla de asociación `servidores_servicios`.
    - Aplicar el filtro de autorización por sección en los listados: `find_all`
      acepta un conjunto de `section_ids` que restringe los resultados a los
      servidores visibles para el usuario autenticado.
    - Combinar datos de usuario (`ServidorCreate`) con datos del sondeo SSH
      (`ServidorInfo`) en la creación atómica de un servidor.
    - Cargar los servicios asociados de forma eficiente (batch IN-query) para
      evitar el problema N+1 en listados.

Qué NO debe contener este fichero:
    - Lógica de negocio ni validaciones de dominio.
    - Sondeo SSH ni comunicación con servidores remotos. Eso pertenece a
      `services/ssh_probe_service.py`.
    - Subida de imágenes a MinIO ni generación de URLs. La transformación de
      `imagen` (nombre de fichero) a `imagen_url` la hace la capa de servicio.
    - Operaciones sobre MongoDB (métricas). Eso pertenece a
      `repositories/mongo_repo.py`.

Relaciones con otros módulos:
    - `models/servidor.py`           → `Servidor`, `ServidorCreate`,
                                       `ServidorPatch`, `ServidorRead`,
                                       `ServidorServicio`.
    - `services/ssh_probe_service.py`→ `ServidorInfo` contiene los campos de
                                       sistema descubiertos por el sondeo SSH
                                       que se persisten en `insert`.
    - `core/database.py`             → proporciona la `Session` inyectada.
    - `core/dependencies.py`         → calcula el `section_ids` visible para el
                                       usuario y lo pasa a `find_all`.
    - `repositories/mongo_repo.py`   → el servicio coordina este repo con
                                       `MongoRepository` para mantener
                                       consistencia cross-BD al renombrar o
                                       eliminar servidores.

Filtro de autorización por sección:
    `find_all` acepta un parámetro `section_ids: set[int] | None`:
        - `None`:      el usuario tiene acceso irrestricto (superadmin); no se
                       aplica ningún filtro WHERE en la query.
        - `set` no vacío: se añade `WHERE seccion_id IN (...)` para devolver
                       solo los servidores de las secciones accesibles.
        - `set` vacío: el usuario no tiene acceso a ninguna sección; la función
                       retorna `([], 0)` inmediatamente sin consultar la BD.

Autor:
    Alejandro Gómez Blanco

Proyecto:
    Metrics Servers

Versión:
    1.0.0

Organización:
    Metrics Servers Project
"""

from __future__ import annotations

from sqlalchemy import delete, func, insert
from sqlmodel import Session, select

from models.servidor import Servidor, ServidorCreate, ServidorPatch, ServidorRead, ServidorServicio
from services.ssh_probe_service import ServidorInfo


class ServidorRepository:
    """
    Repositorio para las tablas `servidores` y `servidores_servicios`.

    Los métodos de escritura gestionan su propio commit/rollback.
    Los de lectura son de solo lectura y no tocan la transacción.
    Los métodos de listado/lectura devuelven `ServidorRead` con `servicios`
    cargados; los helpers de búsqueda puntual devuelven campos individuales
    o el ORM desnudo según las necesidades del llamante.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    # ── Consultas ──────────────────────────────────────────────────────────────

    def find_by_id(self, servidor_id: int) -> ServidorRead | None:
        """
        Busca un servidor por PK y lo devuelve con sus servicios asociados.

        Carga el servidor con `session.get()` (identity map) y a continuación
        resuelve sus servicios asociados con `_get_servicio_ids`. Aunque supone
        una segunda query, el coste es fijo (un único servidor) y aceptable.

        `imagen_url` no se rellena aquí; es responsabilidad del servicio
        llamante resolverla desde MinIO.

        Args:
            servidor_id: Clave primaria del servidor.

        Retorna:
            `ServidorRead` con `servicios` cargados e `imagen_url=None`, o
            `None` si el servidor no existe.
        """
        srv = self.session.get(Servidor, servidor_id)
        if srv is None:
            return None
        servicios_map = self._get_servicio_ids([servidor_id])
        read = self._to_read(srv)
        read.servicios = servicios_map.get(servidor_id, [])
        return read

    def find_all(
        self, offset: int, limit: int, section_ids: set[int] | None = None
    ) -> tuple[list[ServidorRead], int]:
        """
        Devuelve una página de servidores con servicios asociados y el total.

        Filtro de autorización por sección (`section_ids`):
            El parámetro controla qué servidores son visibles para el usuario.
            Ver la sección "Filtro de autorización" del docstring de módulo para
            la semántica completa de `None` vs `set` vacío vs `set` no vacío.

        Optimización anti-N+1:
            Los servicios de todos los servidores de la página se cargan en una
            sola query batch `WHERE servidor_id IN (...)` vía `_get_servicio_ids`,
            evitando una query por servidor.

        Ordenación determinista:
            La query de datos incluye `ORDER BY Servidor.id` para que la
            paginación sea estable aunque haya escrituras concurrentes.

        Condición de carrera:
            El `COUNT(*)` y el `SELECT LIMIT/OFFSET` son queries separadas. Un
            cambio concurrente puede producir una inconsistencia de ±1 en `total`.

        Args:
            offset:      Registros a saltar (= page * size).
            limit:       Máximo de registros a devolver (= size).
            section_ids: Conjunto de IDs de sección accesibles para el usuario,
                         o `None` para acceso irrestricto.

        Retorna:
            Tupla `(lista_de_ServidorRead, total_sin_paginar)`.
        """
        # Guard: empty section set means no accessible sections → short-circuit.
        if section_ids is not None and not section_ids:
            return [], 0

        # COUNT(*) y SELECT LIMIT/OFFSET son dos queries separadas: un INSERT/DELETE
        # concurrente entre ellas puede hacer que `total` no coincida exactamente con
        # len(items). Trade-off aceptable para paginación de lectura sin escritura muy alta.
        count_stmt = select(func.count()).select_from(Servidor)
        data_stmt = select(Servidor).order_by(Servidor.id).offset(offset).limit(limit)
        if section_ids is not None:
            count_stmt = count_stmt.where(Servidor.seccion_id.in_(section_ids))
            data_stmt = data_stmt.where(Servidor.seccion_id.in_(section_ids))

        total: int = self.session.exec(count_stmt).one()
        items = list(self.session.exec(data_stmt).all())
        if not items:
            return [], total
        ids = [s.id for s in items]
        servicios_map = self._get_servicio_ids(ids)  # type: ignore[arg-type]
        result = []
        for s in items:
            read = self._to_read(s)
            read.servicios = servicios_map.get(s.id, [])  # type: ignore[arg-type]
            result.append(read)
        return result, total

    def find_server_id_by_id(self, servidor_id: int) -> str | None:
        """
        Devuelve solo el `server_id` externo de un servidor por PK.

        Proyección puntual: evita cargar y construir el `ServidorRead` completo
        cuando el llamante solo necesita el `server_id` (p. ej. para comparar
        si cambió antes de actualizar MongoDB en `repositories/mongo_repo.py`).

        Retorna:
            El `server_id` string si el servidor existe, `None` si no.
        """
        srv = self.session.get(Servidor, servidor_id)
        return srv.server_id if srv else None

    def find_seccion_id_by_id(self, servidor_id: int) -> int | None:
        """
        Devuelve solo el `seccion_id` de un servidor por PK.

        Proyección puntual usada para verificar a qué sección pertenece un
        servidor sin cargar el objeto completo.

        Retorna:
            El `seccion_id` entero si el servidor existe, `None` si no.
        """
        srv = self.session.get(Servidor, servidor_id)
        return srv.seccion_id if srv else None

    def find_seccion_id_by_server_id(self, server_id: str) -> int | None:
        """
        Devuelve el `seccion_id` de un servidor buscando por `server_id` externo.

        A diferencia de `find_seccion_id_by_id`, busca por el identificador
        externo en lugar del PK interno. Sin índice en `server_id`, esta query
        puede producir un full scan si la tabla de servidores es grande.

        Retorna:
            El `seccion_id` entero si existe un servidor con ese `server_id`,
            `None` si no.
        """
        srv = self.session.exec(
            select(Servidor).where(Servidor.server_id == server_id)
        ).first()
        return srv.seccion_id if srv else None

    def find_imagen_by_id(self, servidor_id: int) -> str | None:
        """
        Devuelve solo el nombre del fichero de imagen de un servidor por PK.

        Proyección puntual usada para obtener el nombre del fichero actual antes
        de borrarlo de MinIO al reemplazarlo con una nueva imagen.

        Retorna:
            El nombre del fichero en MinIO si existe y tiene imagen, `None` si
            el servidor no existe o no tiene imagen asignada.
        """
        srv = self.session.get(Servidor, servidor_id)
        return srv.imagen if srv else None

    def exists(self, servidor_id: int) -> bool:
        """
        Comprueba si existe un servidor con el PK dado.

        Usa `session.get()` (identity map). Se prefiere a `find_by_id() is not None`
        cuando el llamante solo necesita saber si existe sin procesar los datos.

        Retorna:
            True si existe, False si no.
        """
        return self.session.get(Servidor, servidor_id) is not None

    def exists_by_server_id(self, server_id: str) -> bool:
        """
        Comprueba si existe un servidor con el `server_id` externo dado.

        Se usa antes de insertar para detectar duplicados de `server_id` (aunque
        no haya restricción UNIQUE declarada en el modelo).

        Retorna:
            True si existe algún servidor con ese `server_id`, False si no.
        """
        return self.session.exec(
            select(Servidor).where(Servidor.server_id == server_id)
        ).first() is not None

    def find_by_ids(self, ids: list[int]) -> list[Servidor]:
        """
        Devuelve objetos ORM `Servidor` para una lista de PKs.

        Devuelve `Servidor` (ORM desnudo), no `ServidorRead`, porque el
        llamante necesita acceso a los campos ORM para operaciones en lote
        que no requieren la representación HTTP completa.

        Args:
            ids: Lista de PKs. Lista vacía → lista vacía sin query.

        Retorna:
            Lista de objetos `Servidor` ORM. Puede ser más corta que `ids` si
            algún ID no existe.
        """
        if not ids:
            return []
        return list(self.session.exec(select(Servidor).where(Servidor.id.in_(ids))).all())

    # ── Escritura ──────────────────────────────────────────────────────────────

    def insert(self, data: ServidorCreate, probe: ServidorInfo) -> int:
        """
        Inserta un nuevo servidor combinando datos de usuario y datos del sondeo SSH.

        Este método es el único punto de la API donde los dos orígenes de datos
        de un servidor se unen en una única entidad persistida:
            - `data` (`ServidorCreate`): campos de inventario proporcionados
              por el usuario (`server_id`, `dns`, `seccion_id`, `servicios`).
            - `probe` (`ServidorInfo`):  campos de sistema descubiertos por el
              sondeo SSH previo a la creación (`hostname`, `pretty_os`, `arch`,
              `kernel`). Si el sondeo no pudo conectar, estos campos pueden
              ser `None`.

        Usa `session.flush()` tras añadir el servidor para obtener el `id`
        auto-incremental antes de insertar las asociaciones de servicios, todo
        dentro de la misma transacción. Si falla cualquier paso, el rollback
        deshace tanto el servidor como las asociaciones.

        Args:
            data:  DTO de creación con los campos de inventario del usuario.
            probe: Resultado del sondeo SSH con los datos de sistema del servidor.

        Retorna:
            ID entero del servidor recién creado.
        """
        try:
            srv = Servidor(
                server_id=data.server_id,
                dns=data.dns,
                seccion_id=data.seccion_id,
                hostname=probe.hostname,
                pretty_os=probe.pretty_os,
                arch=probe.arch,
                kernel=probe.kernel,
            )
            self.session.add(srv)
            self.session.flush()
            assert srv.id is not None
            if data.servicios:
                self._insert_servicios(srv.id, data.servicios)
            self.session.commit()
            return srv.id
        except Exception:
            self.session.rollback()
            raise

    def update(self, servidor_id: int, patch: ServidorPatch) -> bool:
        """
        Actualiza los campos de un servidor existente (PATCH semántico).

        Usa `patch.model_dump(exclude_none=True, by_alias=False)` + `setattr`.
        El parámetro `by_alias=False` es esencial: `ServidorPatch` declara
        `Field(alias="serverId")` y `Field(alias="prettyOs")` para algunos campos.
        Con `by_alias=True` (o por defecto), `model_dump` devolvería las claves
        en camelCase (`serverId`, `prettyOs`), que no coinciden con los atributos
        Python del ORM (`server_id`, `pretty_os`), causando que `setattr` ignore
        esos campos silenciosamente. Con `by_alias=False` se obtienen las claves
        en snake_case que sí corresponden a los atributos del ORM.

        Se usa tanto para actualizaciones del usuario (`ServidorPatchRequest`
        promovido a `ServidorPatch` en el servicio) como para actualizaciones
        del sondeo SSH (`ServidorPatch` construido por `services/ssh_probe_service.py`).

        Args:
            servidor_id: ID del servidor a actualizar.
            patch:       DTO `ServidorPatch` con los campos a modificar.

        Retorna:
            True si el servidor existía y se actualizó; False si no existe.
        """
        srv = self.session.get(Servidor, servidor_id)
        if srv is None:
            return False
        try:
            data = patch.model_dump(exclude_none=True, by_alias=False)
            for field, value in data.items():
                setattr(srv, field, value)
            self.session.add(srv)
            self.session.commit()
            return True
        except Exception:
            self.session.rollback()
            raise

    def delete(self, servidor_id: int) -> bool:
        """
        Elimina un servidor por PK (ORM-level delete).

        Si la BD tiene `ON DELETE CASCADE` sobre `servidores_servicios`, las
        asociaciones con servicios se eliminan en cascada. Las métricas en
        MongoDB no se eliminan aquí; el servicio llamante debe coordinar la
        llamada a `MongoRepository.delete_by_server_id`.

        Retorna:
            True si existía y se eliminó; False si no existe.
        """
        srv = self.session.get(Servidor, servidor_id)
        if srv is None:
            return False
        try:
            self.session.delete(srv)
            self.session.commit()
            return True
        except Exception:
            self.session.rollback()
            raise

    def delete_bulk(self, ids: list[int]) -> int:
        """
        Elimina múltiples servidores en una única sentencia DELETE.

        Más eficiente que `delete()` en bucle. No activa event listeners ORM
        (es un DELETE SQL directo), por lo que las cascadas deben estar
        definidas en la BD, no en SQLAlchemy. Las métricas en MongoDB tampoco
        se eliminan aquí; el servicio coordinador debe limpiarlas.

        Args:
            ids: Lista de PKs a eliminar. Lista vacía → 0 sin query.

        Retorna:
            Número de filas eliminadas (`rowcount`). Puede ser menor que
            `len(ids)` si algunos IDs no existían.
        """
        if not ids:
            return 0
        try:
            result = self.session.execute(delete(Servidor).where(Servidor.id.in_(ids)))
            self.session.commit()
            return result.rowcount  # type: ignore[return-value]
        except Exception:
            self.session.rollback()
            raise

    def update_imagen(self, servidor_id: int, nombre_archivo: str) -> None:
        """
        Actualiza el nombre del fichero de imagen del servidor.

        Equivalente a `ServicioRepository.update_logo`: persiste el nombre del
        fichero en MinIO tras una subida exitosa. La URL pública se genera en
        la capa de servicio a partir de este nombre.

        Comportamiento si el servidor no existe:
            Retorna `None` silenciosamente. El llamante debe verificar la
            existencia del servidor antes si necesita garantizar la actualización.

        Args:
            servidor_id:    ID del servidor.
            nombre_archivo: Nombre del fichero en el bucket de MinIO.
        """
        srv = self.session.get(Servidor, servidor_id)
        if srv is None:
            return
        try:
            srv.imagen = nombre_archivo
            self.session.add(srv)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    # ── Servicios asociados ────────────────────────────────────────────────────

    def add_servicios(self, servidor_id: int, servicio_ids: list[int]) -> int:
        """
        Asocia servicios a un servidor de forma incremental.

        Usa `INSERT IGNORE` para idempotencia: si algún `servicio_id` ya está
        asociado al servidor (clave duplicada en `servidores_servicios`), se
        ignora sin error. Esto permite llamar al método con listas que contengan
        IDs ya existentes sin necesidad de filtrarlos previamente.

        Args:
            servidor_id:  ID del servidor al que se asocian los servicios.
            servicio_ids: Lista de IDs de servicios a añadir. Lista vacía → 0.

        Retorna:
            Número de IDs enviados a insertar (no el número efectivamente
            insertado, ya que `INSERT IGNORE` puede descartar duplicados sin
            reportarlos en `rowcount`).
        """
        if not servicio_ids:
            return 0
        try:
            self.session.execute(
                insert(ServidorServicio).prefix_with("IGNORE").values(
                    [{"servidor_id": servidor_id, "servicio_id": sid} for sid in servicio_ids]
                )
            )
            self.session.commit()
            return len(servicio_ids)
        except Exception:
            self.session.rollback()
            raise

    def remove_servicios(self, servidor_id: int, servicio_ids: list[int]) -> int:
        """
        Desasocia servicios de un servidor de forma incremental.

        Emite un único `DELETE WHERE servidor_id = ? AND servicio_id IN (?)`
        para eliminar varias asociaciones en una sola operación.

        Args:
            servidor_id:  ID del servidor del que se desasocian los servicios.
            servicio_ids: Lista de IDs de servicios a eliminar. Lista vacía → 0.

        Retorna:
            Número real de filas eliminadas (`rowcount`). Puede ser menor que
            `len(servicio_ids)` si alguna asociación no existía.
        """
        if not servicio_ids:
            return 0
        try:
            result = self.session.execute(
                delete(ServidorServicio).where(
                    ServidorServicio.servidor_id == servidor_id,
                    ServidorServicio.servicio_id.in_(servicio_ids),
                )
            )
            self.session.commit()
            return result.rowcount  # type: ignore[return-value]
        except Exception:
            self.session.rollback()
            raise

    # ── Helpers privados ───────────────────────────────────────────────────────

    def _insert_servicios(self, servidor_id: int, servicio_ids: list[int]) -> None:
        """
        Inserta asociaciones servidor↔servicio sin commit (llamar antes del commit del insert).

        Helper interno para `insert`: añade las asociaciones dentro de la misma
        transacción que crea el servidor, usando `INSERT IGNORE` para tolerancia
        a duplicados. No hace commit; el llamante gestiona la transacción.
        """
        self.session.execute(
            insert(ServidorServicio).prefix_with("IGNORE").values(
                [{"servidor_id": servidor_id, "servicio_id": sid} for sid in servicio_ids]
            )
        )

    def _get_servicio_ids(self, servidor_ids: list[int]) -> dict[int, list[int]]:
        """
        Carga los servicioId asociados a una lista de servidorId en una sola query.

        Usa `WHERE servidor_id IN (...)` para cargar todas las asociaciones de
        los servidores indicados en un único viaje a la BD. Evita el problema N+1
        en `find_by_id` y `find_all`. El resultado se indexa por `servidor_id`
        para asignación O(1).

        Args:
            servidor_ids: Lista de PKs de servidor. Lista vacía → dict vacío.

        Retorna:
            Dict `{servidor_id: [servicio_id, ...]}`.
        """
        if not servidor_ids:
            return {}
        rows = self.session.exec(
            select(ServidorServicio).where(ServidorServicio.servidor_id.in_(servidor_ids))
        ).all()
        result: dict[int, list[int]] = {}
        for row in rows:
            result.setdefault(row.servidor_id, []).append(row.servicio_id)
        return result

    @staticmethod
    def _to_read(srv: Servidor) -> ServidorRead:
        """
        Convierte un ORM `Servidor` en `ServidorRead` con `servicios` vacío.

        Construye el DTO con todos los campos del ORM. El campo `servicios` se
        inicializa como lista vacía `[]`; el llamante lo rellena después con el
        resultado de `_get_servicio_ids`. Esta separación permite cargar los
        servicios en batch para múltiples servidores sin acoplar `_to_read` a la
        lógica de carga batch.

        `imagen_url` no se rellena aquí (queda `None`); la capa de servicio lo
        resuelve desde MinIO antes de devolver la respuesta al cliente.
        """
        return ServidorRead(
            id=srv.id,  # type: ignore[arg-type]
            server_id=srv.server_id,
            dns=srv.dns,
            hostname=srv.hostname,
            pretty_os=srv.pretty_os,
            arch=srv.arch,
            kernel=srv.kernel,
            seccion_id=srv.seccion_id,
            imagen=srv.imagen,
            servicios=[],
        )
