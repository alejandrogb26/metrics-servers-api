"""
Repositorio de acceso a datos para la entidad Sección.

Capa arquitectónica: Infraestructura / Persistencia relacional.

Responsabilidades:
    - Encapsular todas las operaciones CRUD sobre la tabla `secciones`.
    - Devolver objetos ORM `Seccion` directamente; la transformación a
      `SeccionRead` es responsabilidad del llamante (servicio o router).

Qué NO debe contener este fichero:
    - Lógica de negocio ni validaciones de dominio.
    - Gestión de la asociación sección-permisos. Eso pertenece a
      `repositories/grupo_repo.py` (`GrupoSeccion`).
    - Transformación a esquemas de respuesta HTTP.

Relaciones con otros módulos:
    - `models/seccion.py`  → `Seccion` (ORM), `SeccionCreate` y `SeccionPatch`
                             son los tipos que usa este repositorio.
    - `core/database.py`   → proporciona la `Session` inyectada en el constructor.
    - Servicios y routers  → instancian `SeccionRepository(session)` para las
                             operaciones CRUD sobre secciones.

Autor:
    Alejandro Gómez Blanco

Proyecto:
    Metrics Servers

Versión:
    1.0.0

Organización:
    Metrics Servers Project
"""

from sqlalchemy import func
from sqlmodel import Session, select
from models.seccion import Seccion, SeccionCreate, SeccionPatch


class SeccionRepository:
    """
    Repositorio CRUD para la tabla `secciones`.

    Los métodos de escritura gestionan su propio commit/rollback.
    Los de lectura son de solo lectura y no tocan la transacción.
    Todos los métodos devuelven objetos ORM `Seccion`; el llamante convierte
    a `SeccionRead` cuando lo necesite.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def find_by_id(self, seccion_id: int) -> Seccion | None:
        """
        Busca una sección por clave primaria.

        Usa `Session.get()` para aprovechar el identity map de SQLAlchemy:
        si el objeto ya fue cargado en la sesión actual, no se emite ninguna
        query a la BD.

        Args:
            seccion_id: Clave primaria de la sección a buscar.

        Retorna:
            Objeto ORM `Seccion` si existe, `None` si no.
        """
        return self.session.get(Seccion, seccion_id)

    def find_all(self, offset: int, limit: int) -> tuple[list[Seccion], int]:
        """
        Devuelve una página de secciones y el total de registros en la tabla.

        Ejecuta dos queries separadas: `COUNT(*)` para el total y
        `SELECT LIMIT/OFFSET` para la página. El resultado se usa para
        construir un `PagedResponse` en la capa superior.

        Condición de carrera:
            Un cambio concurrente entre ambas queries puede producir una
            inconsistencia de ±1 en `total`. Aceptable dado que las secciones
            son datos de catálogo con baja tasa de escritura.

        Args:
            offset: Registros a saltar (= page * size).
            limit:  Máximo de registros a devolver (= size).

        Retorna:
            Tupla `(lista_de_Seccion, total_sin_paginar)`.
        """
        # COUNT(*) y SELECT LIMIT/OFFSET son dos queries separadas: un INSERT/DELETE
        # concurrente entre ellas puede hacer que `total` no coincida exactamente con
        # len(items). Trade-off aceptable para paginación de lectura sin escritura muy alta.
        total: int = self.session.exec(select(func.count()).select_from(Seccion)).one()
        items = list(self.session.exec(select(Seccion).offset(offset).limit(limit)).all())
        return items, total

    def insert(self, data: SeccionCreate) -> Seccion:
        """
        Inserta una nueva sección y devuelve el objeto ORM con el ID asignado.

        Tras el commit llama a `session.refresh(seccion)` para recargar el
        objeto desde la BD. Esto garantiza que el `id` auto-incremental
        asignado por MariaDB esté disponible en el objeto devuelto. Sin el
        `refresh`, SQLAlchemy podría expirar los atributos del objeto al
        hacer commit, y el llamante recibiría un objeto parcialmente válido.

        A diferencia de `GrupoRepository.insert` (que devuelve solo el `id`
        entero), este método devuelve el objeto `Seccion` completo para que
        el llamante pueda convertirlo directamente a `SeccionRead`.

        Args:
            data: DTO `SeccionCreate` con `nombre` y `descripcion` opcional.

        Retorna:
            Objeto `Seccion` ORM recargado desde la BD, con `id` asignado.
        """
        try:
            seccion = Seccion(nombre=data.nombre, descripcion=data.descripcion)
            self.session.add(seccion)
            self.session.commit()
            # Recarga el objeto desde la base de datos para actualizar los campos generados
            # automáticamente, como el id autoincremental u otros valores por defecto.
            self.session.refresh(seccion)
            return seccion
        except Exception:
            self.session.rollback()
            raise

    def update(self, seccion_id: int, patch: SeccionPatch) -> bool:
        """
        Actualiza los campos de una sección existente (PATCH semántico).

        Usa `patch.model_dump(exclude_none=True)` para obtener solo los campos
        que el cliente envió con valor no-None, y los aplica sobre el objeto
        ORM mediante `setattr`. Este enfoque genérico permite que cualquier
        campo futuro añadido a `SeccionPatch` se procese automáticamente sin
        modificar este método.

        Limitación de `exclude_none=True`:
            No es posible distinguir "campo no enviado" de "campo enviado como
            null". Si se envía `{"descripcion": null}`, el campo se excluirá
            del dump y la descripción no se borrará. Para borrar la descripción
            explícitamente habría que usar `model_fields_set` (como hace
            `GrupoRepository.update` con el campo `dn`). Para los campos de
            `Seccion` actuales (`nombre`, `descripcion`) esto no supone un
            problema práctico porque `nombre` es obligatorio y `descripcion`
            rara vez necesita borrarse explícitamente.

        Args:
            seccion_id: ID de la sección a actualizar.
            patch:      DTO `SeccionPatch` con los campos a modificar.

        Retorna:
            True si la sección existía y se actualizó; False si no existe.
        """
        seccion = self.session.get(Seccion, seccion_id)
        if seccion is None:
            return False
        try:
            # Convierte el DTO SeccionPatch en un diccionario, excluyendo los campos
            # cuyo valor sea None. Así solo se actualizan los campos enviados con valor.
            #
            # Ejemplo:
            # patch = SeccionPatch(nombre="Redes", descripcion=None)
            # data será {"nombre": "Redes"}
            data = patch.model_dump(exclude_none=True)
            
            for key, value in data.items():
                # Asigna dinámicamente el valor al atributo correspondiente del objeto.
                #
                # Por ejemplo:
                # setattr(seccion, "nombre", "Redes")
                # equivale a:
                # seccion.nombre = "Redes"
                setattr(seccion, key, value)
            self.session.add(seccion)
            self.session.commit()
            return True
        except Exception:
            self.session.rollback()
            raise

    def delete(self, seccion_id: int) -> bool:
        """
        Elimina una sección por clave primaria.

        Usa `session.delete()` (ORM-level) para activar los event listeners
        de SQLAlchemy. Si la BD tiene `ON DELETE CASCADE` sobre `grupo_seccion`,
        el borrado se propagará a los permisos de sección del grupo asociados.

        Args:
            seccion_id: ID de la sección a eliminar.

        Retorna:
            True si la sección existía y se eliminó; False si no existe.
        """
        seccion = self.session.get(Seccion, seccion_id)
        if seccion is None:
            return False
        try:
            self.session.delete(seccion)
            self.session.commit()
            return True
        except Exception:
            self.session.rollback()
            raise
