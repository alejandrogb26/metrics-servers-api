"""
Servicio de aplicación para la entidad Sección.

Capa arquitectónica: Aplicación / Servicio.

Responsabilidades:
    - Traducir los parámetros de paginación de la capa HTTP (page, size) a los
      parámetros de repositorio (offset, limit).
    - Convertir los objetos ORM `Seccion` devueltos por el repositorio en DTOs
      `SeccionRead` serializables por FastAPI.
    - Delegar las operaciones de escritura (insert, update, delete) directamente
      en el repositorio sin lógica adicional.

Qué NO debe contener este fichero:
    - Acceso directo a la base de datos. Toda operación de BD pasa por
      `SeccionRepository`.
    - Lógica HTTP ni manejo de excepciones HTTP. Eso pertenece a
      `routers/seccion.py`.
    - Validaciones de dominio más allá de las que ya aplica el repositorio.
      Las secciones son entidades simples (nombre + descripción opcional) sin
      reglas de negocio complejas.

Relaciones con otros módulos:
    - `models/seccion.py`            → `Seccion` (ORM), `SeccionCreate`,
                                       `SeccionPatch`, `SeccionRead`.
    - `repositories/seccion_repo.py` → `SeccionRepository` para todas las
                                       operaciones de BD.
    - `routers/seccion.py`           → instancia `SeccionService(session)` en
                                       cada handler.

Autor:
    Alejandro Gómez Blanco

Proyecto:
    Metrics Servers

Versión:
    1.0.0

Organización:
    Metrics Servers Project
"""

from sqlmodel import Session

from models.seccion import Seccion, SeccionCreate, SeccionPatch, SeccionRead
from repositories.seccion_repo import SeccionRepository


class SeccionService:
    """
    Servicio CRUD para la entidad Sección.

    Capa fina de orquestación: convierte parámetros de paginación en offset/limit
    y mapea objetos ORM a DTOs para los métodos de lectura. Los métodos de
    escritura delegan directamente en el repositorio sin transformación adicional.
    """

    def __init__(self, session: Session) -> None:
        self._repo = SeccionRepository(session)

    def find_by_id(self, seccion_id: int) -> SeccionRead | None:
        """
        Busca una sección por clave primaria y la devuelve como DTO.

        Propaga `None` del repositorio si la sección no existe, para que el
        router pueda elevar `HTTP 404` sin que el servicio conozca el protocolo
        HTTP.

        El `# type: ignore[arg-type]` se debe a la discrepancia entre el `id`
        del ORM (`Optional[int]`) y el `id` de `SeccionRead` (`int`). En runtime
        el `id` siempre es no-None para registros persistidos.

        Args:
            seccion_id: Clave primaria de la sección a recuperar.

        Retorna:
            `SeccionRead` con `id`, `nombre` y `descripcion`, o `None` si no
            existe.
        """
        s = self._repo.find_by_id(seccion_id)
        if s is None:
            return None
        return SeccionRead(id=s.id, nombre=s.nombre, descripcion=s.descripcion)  # type: ignore[arg-type]

    def find_all(self, page: int, size: int) -> tuple[list[SeccionRead], int]:
        """
        Devuelve una página de secciones y el total de registros.

        Convierte los parámetros de paginación base-0 del router (`page`, `size`)
        al par `offset`/`limit` que espera el repositorio:
            offset = page * size

        Mapea cada `Seccion` ORM a `SeccionRead` campo a campo. El
        `# type: ignore[arg-type]` tiene el mismo origen que en `find_by_id`:
        discrepancia `Optional[int]` vs `int` en el campo `id`.

        Args:
            page: Número de página, base 0.
            size: Número máximo de elementos por página.

        Retorna:
            Tupla `(lista_de_SeccionRead, total_sin_paginar)`.
        """
        offset = page * size
        items, total = self._repo.find_all(offset=offset, limit=size)
        return [SeccionRead(id=s.id, nombre=s.nombre, descripcion=s.descripcion) for s in items], total  # type: ignore[arg-type]

    def insert(self, data: SeccionCreate) -> int:
        """
        Crea una nueva sección y devuelve el ID asignado por la base de datos.

        Delega en el repositorio, que hace `session.refresh()` tras el commit
        para garantizar que el `id` auto-incremental está disponible. Este
        servicio extrae y devuelve solo el `id` (no el objeto completo), en
        coherencia con el contrato del router que devuelve `IdResponse`.

        El `# type: ignore[return-value]` se debe a que `s.id` es `Optional[int]`
        en el ORM pero siempre es no-None tras la inserción y el `refresh`.

        Args:
            data: DTO `SeccionCreate` con `nombre` y `descripcion` opcional.

        Retorna:
            `id` auto-incremental asignado a la sección recién creada.
        """
        s = self._repo.insert(data)
        return s.id  # type: ignore[return-value]

    def update(self, seccion_id: int, patch: SeccionPatch) -> bool:
        """
        Actualiza los campos de una sección existente (PATCH semántico).

        Delega directamente en el repositorio. Los campos con valor `None` en
        el patch se excluyen de la actualización (`exclude_none=True` en el
        repositorio), lo que impide borrar `descripcion` enviando `null`.

        Args:
            seccion_id: ID de la sección a actualizar.
            patch:      DTO `SeccionPatch` con los campos a modificar.

        Retorna:
            `True` si la sección existía y se actualizó; `False` si no existe.
        """
        return self._repo.update(seccion_id, patch)

    def delete(self, seccion_id: int) -> bool:
        """
        Elimina una sección por clave primaria.

        Delega directamente en el repositorio (borrado ORM con cascada).

        Args:
            seccion_id: ID de la sección a eliminar.

        Retorna:
            `True` si la sección existía y se eliminó; `False` si no existe.
        """
        return self._repo.delete(seccion_id)
