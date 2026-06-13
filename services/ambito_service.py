"""
Servicio de aplicación para la entidad Ámbito.

Capa arquitectónica: Aplicación / Servicio.

Responsabilidades:
    - Traducir los parámetros de paginación de la capa HTTP (page, size) a los
      parámetros de repositorio (offset, limit).
    - Convertir los objetos ORM `Ambito` devueltos por el repositorio en DTOs
      `AmbitoRead` serializables por FastAPI.
    - Actuar como punto de indirección entre el router y el repositorio, de modo
      que ninguna capa dependa directamente de la otra.

Qué NO debe contener este fichero:
    - Acceso directo a la base de datos. Toda operación de BD pasa por
      `AmbitoRepository`.
    - Lógica HTTP ni manejo de excepciones HTTP. Eso pertenece a
      `routers/ambito.py`.
    - Escritura ni modificación de ámbitos. Los ámbitos son catálogo de solo
      lectura; no existen métodos de creación, actualización ni borrado.

Relaciones con otros módulos:
    - `models/ambito.py`         → `AmbitoRead` como DTO de salida.
    - `repositories/ambito_repo.py` → `AmbitoRepository` para el acceso a BD.
    - `routers/ambito.py`        → instancia `AmbitoService(session)` en cada
                                   handler.

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

from exceptions.errors import NotFoundException
from models.ambito import AmbitoRead
from repositories.ambito_repo import AmbitoRepository


class AmbitoService:
    """
    Servicio de solo lectura para la entidad Ámbito.

    Capa fina de orquestación: convierte parámetros de paginación en offset/limit
    y mapea objetos ORM a DTOs. No contiene lógica de negocio propia porque los
    ámbitos son datos de catálogo inmutables desde la perspectiva de la API.
    """

    def __init__(self, session: Session) -> None:
        self._repo = AmbitoRepository(session)

    def get_all(self, page: int, size: int) -> tuple[list[AmbitoRead], int]:
        """
        Devuelve una página de ámbitos y el total de registros en la tabla.

        Convierte los parámetros de paginación base-0 del router (`page`, `size`)
        al par `offset`/`limit` que espera el repositorio:
            offset = page * size

        Mapea cada `Ambito` ORM a un `AmbitoRead` explícitamente campo a campo.
        El `# type: ignore[arg-type]` se debe a que el ORM declara `id` como
        `Optional[int]` (necesario para que SQLModel acepte objetos sin PK antes
        de persistirlos), pero `AmbitoRead.id` espera `int`. En runtime `id`
        siempre es no-None para registros ya persistidos.

        Args:
            page: Número de página, base 0.
            size: Número máximo de elementos por página.

        Retorna:
            Tupla `(lista_de_AmbitoRead, total_sin_paginar)`.
        """
        offset = page * size
        items, total = self._repo.find_all(offset=offset, limit=size)
        return [AmbitoRead(id=a.id, nombre=a.nombre, descripcion=a.descripcion) for a in items], total  # type: ignore[arg-type]

    def get_by_id(self, ambito_id: int) -> AmbitoRead:
        """
        Busca un ámbito por su clave primaria y lo devuelve como DTO.

        Args:
            ambito_id: Clave primaria del ámbito a recuperar.

        Retorna:
            `AmbitoRead` con id, nombre y descripción del ámbito.

        Lanza:
            `NotFoundException` si no existe un ámbito con `ambito_id`.
        """
        a = self._repo.find_by_id(ambito_id)
        if a is None:
            raise NotFoundException(f"Ámbito con id={ambito_id} no encontrado")
        return AmbitoRead(id=a.id, nombre=a.nombre, descripcion=a.descripcion)  # type: ignore[arg-type]
