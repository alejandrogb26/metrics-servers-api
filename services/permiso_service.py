"""
Servicio de aplicación para la entidad Permiso.

Capa arquitectónica: Aplicación / Servicio.

Responsabilidades:
    - Traducir los parámetros de paginación de la capa HTTP (page, size) a los
      parámetros de repositorio (offset, limit).
    - Actuar como punto de indirección entre el router y el repositorio, de modo
      que ninguna capa dependa directamente de la otra.

Qué NO debe contener este fichero:
    - Acceso directo a la base de datos. Toda operación de BD pasa por
      `PermisoRepository`.
    - Lógica HTTP ni manejo de excepciones HTTP. Eso pertenece a
      `routers/permiso.py`.
    - Transformación ORM → DTO. `PermisoRepository` ya devuelve `PermisoRead`
      directamente (con el ámbito embebido), por lo que este servicio no
      necesita realizar ningún mapeo.
    - Escritura ni modificación de permisos. Los permisos son catálogo de solo
      lectura; no existen métodos de creación, actualización ni borrado.

Relaciones con otros módulos:
    - `models/permiso.py`            → `PermisoRead` como tipo de retorno.
    - `repositories/permiso_repo.py` → `PermisoRepository` para el acceso a BD.
    - `routers/permiso.py`           → instancia `PermisoService(session)` en
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

from models.permiso import PermisoRead
from repositories.permiso_repo import PermisoRepository


class PermisoService:
    """
    Servicio de solo lectura para la entidad Permiso.

    Capa más delgada del proyecto: no realiza mapeo ORM→DTO (el repositorio
    ya devuelve `PermisoRead` directamente mediante JOIN con `Ambito`) y no
    contiene lógica de negocio. Su única contribución es el cálculo del
    `offset` de paginación y la indirección arquitectónica entre el router
    y el repositorio.
    """

    def __init__(self, session: Session) -> None:
        self._repo = PermisoRepository(session)

    def get_all(self, page: int, size: int) -> tuple[list[PermisoRead], int]:
        """
        Devuelve una página de permisos con sus ámbitos embebidos y el total.

        Convierte los parámetros de paginación base-0 del router (`page`, `size`)
        al par `offset`/`limit` que espera el repositorio:
            offset = page * size

        El repositorio devuelve `PermisoRead` directamente (con `AmbitoRead`
        embebido) ordenados por `(Ambito.nombre, Permiso.nombre)`, sin necesidad
        de transformación adicional en este servicio.

        Args:
            page: Número de página, base 0.
            size: Número máximo de elementos por página.

        Retorna:
            Tupla `(lista_de_PermisoRead, total_sin_paginar)`. Cada `PermisoRead`
            incluye el `AmbitoRead` completo embebido.
        """
        offset = page * size
        return self._repo.find_all(offset=offset, limit=size)

    def get_by_id(self, permiso_id: int) -> PermisoRead | None:
        """
        Busca un permiso por su clave primaria y lo devuelve con el ámbito embebido.

        Propaga `None` del repositorio si el permiso no existe, para que el
        router pueda elevar `HTTP 404` sin que el servicio conozca el protocolo
        HTTP.

        Args:
            permiso_id: Clave primaria del permiso a recuperar.

        Retorna:
            `PermisoRead` con ámbito embebido si existe, `None` si no.
        """
        return self._repo.find_by_id(permiso_id)
