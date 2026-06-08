"""
Modelos y esquemas Pydantic de uso transversal en la API.

Capa arquitectónica: Dominio / Contratos HTTP comunes.

Responsabilidades:
    - Definir los esquemas de respuesta reutilizables que no pertenecen a una
      entidad concreta: paginación (`PagedResponse`), operaciones masivas
      (`BulkResult`), respuestas de identificador (`IdResponse`), conteo
      (`CountResult`), subida de imágenes (`UploadResult`).
    - Definir los contratos de autenticación: petición de login (`LoginRequest`),
      respuesta de login (`LoginResponse`) y datos de sesión activa
      (`SessionResponse`).
    - Centralizar la configuración de serialización camelCase para que todos
      los modelos de respuesta HTTP usen un formato uniforme.

Qué NO debe contener este fichero:
    - Modelos ORM con `table=True`. Los modelos de este fichero son solo
      esquemas Pydantic para validación y serialización HTTP.
    - Lógica de negocio ni acceso a base de datos.
    - Modelos específicos de una entidad (servidor, grupo, ámbito, etc.).
      Cada entidad tiene su propio fichero en `models/`.

Relaciones con otros módulos:
    - Todos los routers             → usan `PagedResponse[X]`, `BulkResult`,
                                      `IdResponse` y `CountResult` como
                                      `response_model` en endpoints paginados
                                      o de operaciones masivas.
    - `routers/auth.py`             → usa `LoginRequest`, `LoginResponse` y
                                      `SessionResponse` como esquemas de
                                      entrada y salida del flujo de autenticación.
    - `services/auth_service.py`    → construye `LoginResponse` y `SessionResponse`
                                      tras validar las credenciales y emitir el JWT.
    - `services/minio_service.py`   → devuelve `UploadResult` tras subir una
                                      imagen a MinIO.

Autor:
    Alejandro Gómez Blanco

Proyecto:
    Metrics Servers

Versión:
    1.0.0

Organización:
    Metrics Servers Project
"""

import math
from typing import Generic, Optional, TypeVar
from pydantic import BaseModel, ConfigDict, computed_field
from pydantic.alias_generators import to_camel

# Define una variable de tipo genérico llamada T.
# Se usa para que PagedResponse pueda parametrizar el tipo de los elementos
# incluidos en el campo `data`.
#
# Ejemplos:
# - PagedResponse[ServidorRead] -> data será list[ServidorRead]
# - PagedResponse[GrupoRead]    -> data será list[GrupoRead]
T = TypeVar("T")

# Configuración Pydantic compartida por todos los modelos de respuesta HTTP.
# - `alias_generator=to_camel`: serializa los campos en camelCase en el JSON
#   de respuesta (p. ej. `total_pages` → `totalPages`, `url_foto` → `urlFoto`).
#   Los clientes Flutter y Swing esperan camelCase.
# - `populate_by_name=True`: permite instanciar los modelos usando tanto el
#   nombre Python snake_case como el alias camelCase, necesario para construir
#   respuestas desde código interno que usa snake_case.
_camel = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class PagedResponse(BaseModel, Generic[T]):
    """
    Respuesta HTTP genérica paginada para listados de cualquier entidad.

    Modelo genérico parametrizado con `T` (el tipo del elemento en la lista).
    FastAPI y Pydantic resuelven el tipo concreto al declarar
    `response_model=PagedResponse[ServidorRead]` en el router.

    Campos de entrada (provistos al construir el objeto):
        data  (list[T]): Página actual de elementos.
        page  (int):     Índice de página actual, con base 0.
        size  (int):     Número de elementos por página solicitado.
        total (int):     Total de elementos en el dataset completo (sin paginar).

    Campos calculados (derivados, incluidos en la respuesta JSON):
        total_pages (int):  Total de páginas disponibles para `size` dado.
        has_next    (bool): True si existe al menos una página posterior a la actual.

    Advertencia de rendimiento:
        El método de fábrica `of` implementa paginación en memoria: recibe la
        lista completa de elementos y la corta en Python. Esto requiere que el
        repositorio/servicio cargue todos los registros de la BD antes de paginar.
        Para datasets pequeños es aceptable, pero para tablas con miles de filas
        se debería migrar a paginación a nivel de consulta SQL (LIMIT/OFFSET).
    """

    model_config = _camel

    data: list[T]
    page: int
    size: int
    total: int

    @computed_field     # Indica a Pydantic que esa propiedad calculada debe aparecer en la salida del modelo.
    @property           # Convierte el método en una propiedad.   
    def total_pages(self) -> int:
        """
        Número total de páginas para el tamaño de página actual.

        Usa `math.ceil` para redondear hacia arriba: si hay 11 elementos y
        `size=10`, el resultado es 2 páginas (la segunda con un solo elemento).
        El guard `if self.size > 0` previene la división por cero, aunque el
        método `of` ya garantiza `size >= 1` mediante el clamping `max(1, ...)`.
        """
        # Si el 'size' es mayor que 0, hago el math.    
        return math.ceil(self.total / self.size) if self.size > 0 else 0

    @computed_field
    @property
    def has_next(self) -> bool:
        """
        Indica si existe una página siguiente a la actual.

        Fórmula (paginación de base 0):
            `(page + 1) * size < total`

        Ejemplo: page=0, size=10, total=10 → `(0+1)*10 < 10` → False (sin siguiente).
        Ejemplo: page=0, size=10, total=11 → `(0+1)*10 < 11` → True (hay segunda página).
        """
        return (self.page + 1) * self.size < self.total

    @classmethod
    def of(cls, items: list[T], page: int, size: int) -> "PagedResponse[T]":
        """
        Método de fábrica que pagina en memoria una lista completa de elementos.

        Normaliza los parámetros de paginación para prevenir entradas inválidas
        o abusivas antes de cortar la lista:

            safe_size = max(1, min(size, 100))
                → Clampea el tamaño de página entre 1 y 100. Previene que un
                  cliente solicite 0 elementos (división por cero en `total_pages`)
                  o un número arbitrariamente grande (p. ej. 100.000) que cargue
                  toda la tabla en una sola respuesta HTTP.

            safe_page = max(0, page)
                → Previene páginas con índice negativo. No existe límite superior
                  al número de página; si `page` supera el máximo existente, `data`
                  será una lista vacía (el slice produce `[]`).

        El campo `total` del resultado refleja el número total de elementos en
        `items` antes de la paginación, no el número de elementos devueltos.
        Esto permite al cliente calcular cuántas páginas existen en total.

        Args:
            items: Lista completa de elementos a paginar. El método no consulta
                   ninguna base de datos; asume que `items` ya contiene todos
                   los registros relevantes.
            page:  Índice de página solicitado, con base 0.
            size:  Número de elementos por página solicitado (se clampea a [1, 100]).

        Retorna:
            PagedResponse[T] con la página indicada de `items`, los parámetros
            de paginación normalizados y el total del dataset completo.
        """
        safe_size = max(1, min(size, 100))
        safe_page = max(0, page)
        total = len(items)
        start = min(safe_page * safe_size, total)
        end = min(start + safe_size, total)
        return cls(data=items[start:end], page=safe_page, size=safe_size, total=total)


class BulkResult(BaseModel):
    """
    Resultado de una operación masiva (bulk) sobre múltiples elementos.

    Se usa como respuesta de endpoints que procesan una lista de entidades de
    forma individual y necesitan informar al cliente de cuántas operaciones
    tuvieron éxito y cuántas fallaron, junto con los mensajes de error de cada
    fallo.

    Todos los campos tienen valores por defecto para permitir construir el
    resultado de forma incremental, acumulando contadores y errores en un bucle:

        result = BulkResult()
        for item in items:
            try:
                procesar(item)
                result.ok += 1
            except Exception as e:
                result.failed += 1
                result.errors.append(str(e))
        result.total = result.ok + result.failed

    Campos:
        total  (int):       Total de elementos procesados.
        ok     (int):       Número de operaciones completadas con éxito.
        failed (int):       Número de operaciones fallidas.
        errors (list[str]): Mensajes de error individuales de los elementos fallidos.
    """

    model_config = _camel

    total: int = 0
    ok: int = 0
    failed: int = 0
    errors: list[str] = []


class IdResponse(BaseModel):
    """
    Respuesta HTTP minimalista que devuelve solo el identificador del recurso creado.

    Se usa en endpoints de creación (POST) que no necesitan devolver el objeto
    completo, sino solo confirmar el ID asignado por la base de datos al nuevo
    registro. Esto reduce el payload de respuesta y evita hacer una segunda
    consulta de lectura solo para construir la respuesta completa.

    Campos:
        id (int): Identificador auto-incremental asignado por la BD al recurso.
    """

    model_config = _camel

    id: int


class CountResult(BaseModel):
    """
    Respuesta estándar para operaciones de asociación/desasociación.

    Devuelve el número de elementos afectados por una operación de relación
    (p. ej. cuántos permisos se asociaron a un grupo, cuántos servidores
    se desasociaron de una sección). Permite al cliente confirmar que la
    operación tuvo el efecto esperado sin necesidad de recargar el recurso.

    Campos:
        count (int): Número de filas o relaciones afectadas por la operación.
    """

    count: int


class UploadResult(BaseModel):
    """
    Respuesta de endpoints de subida de imagen a MinIO.

    Devuelve el nombre del fichero almacenado y la URL pública (o firmada)
    para acceder a la imagen subida. Se usa tras completar la subida de
    avatares de usuario, capturas de servidores o iconos de servicios.

    Campos:
        nombre_archivo (str):       Nombre del fichero tal como quedó almacenado
                                    en el bucket de MinIO. Puede diferir del nombre
                                    original si se renombró para evitar colisiones.
        url_foto       (str | None): URL de acceso a la imagen. Puede ser None si
                                    MinIO no está configurado para exponer URLs
                                    públicas o si la URL se genera de forma diferida.
    """

    model_config = _camel

    nombre_archivo: str
    url_foto: Optional[str] = None


class LoginRequest(BaseModel):
    """
    Cuerpo de la petición de login (credenciales del usuario).

    No aplica serialización camelCase (`model_config = _camel`) porque los
    campos `username` y `password` ya son lowercase y no necesitan alias.
    Pydantic los valida directamente del JSON de la petición sin transformación.

    Campos:
        username (str): Nombre de usuario (login del directorio LDAP).
        password (str): Contraseña en texto claro. Se transmite solo sobre HTTPS.
                        La API no almacena la contraseña; se usa exclusivamente
                        para autenticar contra el servidor LDAP y se descarta.
    """

    username: str
    password: str


class LoginResponse(BaseModel):
    """
    Respuesta del endpoint de login tras autenticación exitosa.

    Incluye el token JWT para autenticar peticiones posteriores y, opcionalmente,
    los datos de la sesión activa para que el cliente no tenga que hacer una
    segunda petición a `/auth/session` inmediatamente después del login.

    Campos:
        token      (str):           Token JWT firmado. El cliente debe incluirlo
                                    en la cabecera `Authorization: Bearer <token>`
                                    en todas las peticiones autenticadas.
        token_type (str):           Tipo de token. Siempre `"Bearer"` por defecto,
                                    conforme al estándar OAuth2 / RFC 6750.
        expires_in (int):           Tiempo de vida del token en segundos. Permite
                                    al cliente calcular cuándo el token expirará
                                    y programar un refresco si lo implementa.
        session    (dict | None):   Datos de la sesión activa del usuario (nombre,
                                    email, grupo, permisos, foto). Se incluyen
                                    opcionalmente para que el cliente pueda
                                    inicializar su estado en un solo round-trip.
                                    La estructura real del dict sigue el esquema
                                    de `SessionResponse`.
    """

    model_config = _camel

    token: str
    token_type: str = "Bearer"
    expires_in: int
    session: Optional[dict] = None


class SessionResponse(BaseModel):
    """
    Datos de la sesión activa del usuario autenticado.

    Se devuelve en el endpoint `/auth/session` para que el cliente recupere
    el estado del usuario sin necesidad de re-decodificar el JWT ni de
    realizar consultas adicionales. También se embebe opcionalmente en
    `LoginResponse.session` para reducir los round-trips en el arranque.

    Los campos `grupo` y `permisos` se tipan como `dict` libre en lugar de
    usar modelos Pydantic específicos para mantener flexibilidad: su estructura
    puede variar según el grupo del usuario y los permisos asignados, y en
    el momento del diseño se priorizó evitar el acoplamiento con los modelos
    de `models/grupo.py` y `models/permiso.py`.

    Campos:
        username     (str):          Nombre de usuario (login). Corresponde al
                                     claim `sub` del JWT.
        display_name (str | None):   Nombre visible del usuario, obtenido de
                                     LDAP. None si el atributo no existe en el
                                     directorio.
        email        (str | None):   Correo electrónico desde LDAP. None si
                                     no está disponible.
        grupo        (dict | None):  Datos del grupo al que pertenece el usuario
                                     (id, nombre, ámbito, etc.). None si el
                                     usuario no tiene grupo asignado.
        permisos     (dict | None):  Mapa de permisos del grupo del usuario,
                                     organizado por sección. None si no hay
                                     permisos cargados.
        url_foto     (str | None):   URL del avatar del usuario en MinIO. None
                                     si el usuario no tiene foto de perfil.
    """

    model_config = _camel

    username: str
    display_name: Optional[str] = None
    email: Optional[str] = None
    grupo: Optional[dict] = None
    permisos: Optional[dict] = None
    url_foto: Optional[str] = None
