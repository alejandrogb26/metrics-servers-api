"""
Repositorio de acceso a datos para la entidad Servicio.

Capa arquitectónica: Infraestructura / Persistencia relacional.

Responsabilidades:
    - Encapsular todas las operaciones CRUD sobre la tabla `servicios`.
    - Separar la actualización del logo (`update_logo`) del resto del ciclo
      de vida del servicio, coherente con el diseño de `models/servicio.py`
      donde `logo` se gestiona mediante un endpoint dedicado.

Qué NO debe contener este fichero:
    - Lógica de negocio ni validaciones de dominio.
    - Subida ni gestión de ficheros en MinIO. Eso pertenece a
      `services/minio_service.py`.
    - Generación de `url_logo`. El repositorio almacena y devuelve el nombre
      del fichero (`logo`); la transformación a URL la hace la capa de servicio.
    - Gestión de la asociación servicio-servidor (`servidores_servicios`). Eso
      pertenece a `repositories/servidor_repo.py`.

Relaciones con otros módulos:
    - `models/servicio.py`  → `Servicio` (ORM), `ServicioCreate` y `ServicioPatch`.
    - `core/database.py`    → proporciona la `Session` inyectada en el constructor.
    - Servicios y routers   → instancian `ServicioRepository(session)` para las
                              operaciones sobre servicios.
    - `services/minio_service.py` → sube el fichero de logo a MinIO y luego llama
                                    a `update_logo` para persistir el nombre del
                                    fichero en la BD.

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
from models.servicio import Servicio, ServicioCreate, ServicioPatch


class ServicioRepository:
    """
    Repositorio CRUD para la tabla `servicios`.

    Los métodos de escritura gestionan su propio commit/rollback.
    Los de lectura son de solo lectura y no tocan la transacción.
    Todos los métodos devuelven objetos ORM `Servicio`; la transformación
    a `ServicioRead` (con `url_logo`) es responsabilidad del llamante.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def find_by_id(self, servicio_id: int) -> Servicio | None:
        """
        Busca un servicio por clave primaria.

        Usa `Session.get()` para aprovechar el identity map de SQLAlchemy.

        Args:
            servicio_id: Clave primaria del servicio a buscar.

        Retorna:
            Objeto ORM `Servicio` si existe, `None` si no.
        """
        return self.session.get(Servicio, servicio_id)

    def find_all(self, offset: int, limit: int) -> tuple[list[Servicio], int]:
        """
        Devuelve una página de servicios y el total de registros en la tabla.

        Ejecuta dos queries separadas: `COUNT(*)` para el total y
        `SELECT LIMIT/OFFSET` para la página.

        Condición de carrera:
            Un cambio concurrente entre ambas queries puede producir una
            inconsistencia de ±1 en `total`. Aceptable dado que los servicios
            son datos de catálogo con baja tasa de escritura.

        Args:
            offset: Registros a saltar (= page * size).
            limit:  Máximo de registros a devolver (= size).

        Retorna:
            Tupla `(lista_de_Servicio, total_sin_paginar)`.
        """
        # COUNT(*) y SELECT LIMIT/OFFSET son dos queries separadas: un INSERT/DELETE
        # concurrente entre ellas puede hacer que `total` no coincida exactamente con
        # len(items). Trade-off aceptable para paginación de lectura sin escritura muy alta.
        total: int = self.session.exec(select(func.count()).select_from(Servicio)).one()
        items = list(self.session.exec(select(Servicio).offset(offset).limit(limit)).all())
        return items, total

    def insert(self, data: ServicioCreate) -> Servicio:
        """
        Inserta un nuevo servicio y devuelve el objeto ORM con el ID asignado.

        Solo establece `nombre` al crear el servicio. El campo `logo` se omite
        deliberadamente: el logo se asigna después de la creación mediante el
        endpoint dedicado de subida, que llama a `update_logo`. Esto es coherente
        con el ciclo de vida separado del logo definido en `models/servicio.py`.

        Llama a `session.refresh(servicio)` tras el commit para recargar el `id`
        auto-incremental asignado por la BD y garantizar que el objeto devuelto
        esté completamente actualizado.

        Args:
            data: DTO `ServicioCreate` con el `nombre` del servicio.

        Retorna:
            Objeto `Servicio` ORM recargado desde la BD, con `id` asignado.
        """
        try:
            servicio = Servicio(nombre=data.nombre)
            self.session.add(servicio)
            self.session.commit()
            self.session.refresh(servicio)
            return servicio
        except Exception:
            self.session.rollback()
            raise

    def update(self, servicio_id: int, patch: ServicioPatch) -> bool:
        """
        Actualiza los campos editables de un servicio existente (PATCH semántico).

        Usa `patch.model_dump(exclude_none=True)` + `setattr` para aplicar
        solo los campos no-None del DTO sobre el objeto ORM. El campo `logo`
        no forma parte de `ServicioPatch`, por lo que este método nunca lo
        modifica; eso es responsabilidad exclusiva de `update_logo`.

        Args:
            servicio_id: ID del servicio a actualizar.
            patch:       DTO `ServicioPatch` con los campos a modificar.

        Retorna:
            True si el servicio existía y se actualizó; False si no existe.
        """
        servicio = self.session.get(Servicio, servicio_id)
        if servicio is None:
            return False
        try:
            data = patch.model_dump(exclude_none=True)
            for key, value in data.items():
                setattr(servicio, key, value)
            self.session.add(servicio)
            self.session.commit()
            return True
        except Exception:
            self.session.rollback()
            raise

    def update_logo(self, servicio_id: int, nombre_archivo: str) -> None:
        """
        Actualiza el nombre del fichero de logo del servicio en la BD.

        Método dedicado para la gestión del logo, coherente con el endpoint
        separado `POST /{id}/logo`. Se llama desde el servicio de MinIO tras
        subir el fichero, para persistir el nombre del fichero resultante.

        Solo actualiza el campo `logo` (nombre del fichero en MinIO). La
        generación de la URL pública a partir de ese nombre es responsabilidad
        de la capa de servicio al construir `ServicioRead`.

        Comportamiento si el servicio no existe:
            Retorna `None` silenciosamente sin modificar nada. A diferencia de
            `update` y `delete` (que devuelven `False` para señalar ausencia),
            este método no comunica al llamante si el servicio existía o no.
            El llamante debe verificar la existencia del servicio antes de llamar
            a este método si necesita garantizar que la actualización se realizó.

        Args:
            servicio_id:    ID del servicio cuyo logo se actualiza.
            nombre_archivo: Nombre del fichero tal como quedó almacenado en
                            el bucket de MinIO.
        """
        servicio = self.session.get(Servicio, servicio_id)
        if servicio is None:
            return
        try:
            servicio.logo = nombre_archivo
            self.session.add(servicio)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def delete(self, servicio_id: int) -> bool:
        """
        Elimina un servicio por clave primaria.

        Usa `session.delete()` (ORM-level). Si la BD tiene `ON DELETE CASCADE`
        sobre `servidores_servicios`, la eliminación se propagará a las
        asociaciones servidor-servicio existentes.

        Args:
            servicio_id: ID del servicio a eliminar.

        Retorna:
            True si el servicio existía y se eliminó; False si no existe.
        """
        servicio = self.session.get(Servicio, servicio_id)
        if servicio is None:
            return False
        try:
            self.session.delete(servicio)
            self.session.commit()
            return True
        except Exception:
            self.session.rollback()
            raise
