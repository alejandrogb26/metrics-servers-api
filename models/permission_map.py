"""
Modelo genérico que agrupa permisos globales y permisos de sección.

Capa arquitectónica: Dominio / Contratos HTTP comunes.

Responsabilidades:
    - Proporcionar un contenedor tipado y serializable para representar el mapa
      completo de permisos de un grupo: tanto los permisos que aplican a todo
      el sistema (`global_perms`) como los que aplican por sección (`sections`).
    - Ser parametrizable con el tipo del elemento de permiso: `PermissionMap[int]`
      cuando se trabaja con IDs (en operaciones de escritura y en JWT), o
      `PermissionMap[PermisoRead]` si en el futuro se necesita embeber objetos
      completos en la respuesta.

Qué NO debe contener este fichero:
    - Lógica de evaluación de permisos. La comprobación de si un usuario tiene
      un permiso concreto pertenece a `core/dependencies.py`.
    - Acceso a base de datos ni referencias a modelos ORM.
    - Tipos de permiso concretos. La parametrización con `Generic[T]` mantiene
      este modelo desacoplado de `Permiso` y `PermisoRead`.

Relaciones con otros módulos:
    - `models/grupo.py`          → `GrupoCreate` y `GrupoRead` usan
                                   `PermissionMap[int]` para enviar y recibir
                                   permisos como listas de IDs enteros.
    - `core/dependencies.py`     → construye un `PermissionMap[int]` a partir
                                   de los registros de `GrupoPermisoGlobal` y
                                   `GrupoSeccion` para compararlo con los
                                   permisos requeridos por cada endpoint.
    - `services/auth_service.py` → puede incluir el mapa de permisos del grupo
                                   en la respuesta de sesión (`SessionResponse`).

Estructura del mapa de permisos:
    El sistema distingue dos niveles de permisos (ver `models/grupo.py` para
    la definición de las tablas de asociación correspondientes):

        global_perms: list[T] | None
            Permisos que aplican a todo el sistema sin restricción de sección.
            Representados como lista plana de elementos de tipo T.

        sections: dict[int, list[T]] | None
            Permisos organizados por sección. La clave es el `seccion_id`
            (entero) y el valor es la lista de permisos que el grupo tiene
            para esa sección concreta.

    Ambos campos son opcionales (`None`). Un grupo puede tener solo permisos
    globales, solo permisos de sección, ambos, o ninguno (mapa vacío).
"""

from typing import Generic, Optional, TypeVar
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

# Variable de tipo genérico para el elemento de permiso.
# Permite que PermissionMap funcione tanto con IDs enteros (uso habitual)
# como con objetos PermisoRead completos (si se necesita en el futuro).
T = TypeVar("T")


class PermissionMap(BaseModel, Generic[T]):
    """
    Contenedor genérico para el mapa de permisos de un grupo.

    Agrupa en un solo objeto los dos niveles del sistema de autorización:
    permisos globales (sin restricción de sección) y permisos por sección
    (con clave `seccion_id`). La serialización a JSON usa camelCase para
    compatibilidad con los clientes Flutter y Swing.

    Parametrización:
        `PermissionMap[int]`
            Uso habitual: los elementos de permiso son IDs enteros (claves
            primarias de la tabla `permisos`). Se usa en `GrupoCreate`,
            `GrupoRead` y en la construcción del mapa de permisos del usuario
            en `core/dependencies.py`.

        `PermissionMap[PermisoRead]`
            Uso potencial: los elementos son objetos `PermisoRead` completos
            con nombre, descripción y ámbito embebidos. No se usa actualmente
            pero el tipo genérico lo soporta sin cambios en este modelo.

    Serialización camelCase:
        - `global_perms` → `globalPerms` en JSON.
        - `sections`     → `sections` en JSON (sin cambio, ya es una sola palabra).
        `populate_by_name=True` permite construir el objeto con el nombre Python
        original (`global_perms`) desde código interno.

    Campos:
        global_perms (list[T] | None): Permisos que aplican a todo el sistema.
                                       None si el grupo no tiene permisos globales
                                       o si no se han cargado.
        sections     (dict[int, list[T]] | None): Mapa de permisos por sección.
                                       Clave: ID de sección. Valor: lista de
                                       permisos del grupo para esa sección.
                                       None si el grupo no tiene permisos de
                                       sección o si no se han cargado.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    global_perms: Optional[list[T]] = None
    sections: Optional[dict[int, list[T]]] = None
