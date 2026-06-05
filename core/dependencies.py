"""
Módulo de dependencias de autenticación y autorización para FastAPI.

Capa arquitectónica: Infraestructura / Seguridad transversal.

Responsabilidades:
    - Validar el token JWT presente en la cabecera `Authorization` de cada
      petición protegida y construir un objeto `RequestUser` con los datos del
      usuario autenticado (`get_current_user`).
    - Comprobar si un token ha sido revocado consultando la blocklist en Redis
      (`core/token_blocklist.py`), lo que habilita el logout efectivo pese a la
      naturaleza stateless de JWT.
    - Verificar que el usuario autenticado posee al menos uno de los permisos
      requeridos para acceder a un endpoint determinado (`require_permission`).
    - Proporcionar a los endpoints la lista de secciones (IDs) visibles para un
      usuario dado un permiso concreto (`visible_section_ids`), soporte necesario
      para el filtrado de datos por sección en los clientes Flutter y Swing.

Qué NO debe contener este fichero:
    - Lógica de negocio de ningún tipo.
    - Emisión de tokens JWT (eso pertenece a `services/auth_service.py`).
    - Definición de modelos de respuesta HTTP (eso pertenece a `models/`).
    - Configuración del motor de base de datos (eso pertenece a `core/database.py`).

Equivalencia con el backend Java/JAX-RS:
    Este módulo reproduce el comportamiento de dos filtros del backend anterior:
    - `TokenFilter`         → `get_current_user`: valida la firma y vigencia del JWT.
    - `AuthorizationFilter` → `require_permission`: comprueba permisos del grupo.

Relaciones con otros módulos:
    - `core/security.py`         → `decode_token` verifica la firma HMAC del JWT.
    - `core/token_blocklist.py`  → `is_revoked` consulta la blocklist Redis por JTI.
    - `repositories/grupo_repo.py` → carga permisos globales y por sección del grupo
                                     del usuario (lazy, solo cuando se necesita).
    - `core/database.py`         → `engine` se usa en `_load_perms_if_needed` para
                                   abrir una sesión de BD propia.
    - Todos los `routers/`       → consumen `get_current_user` y `require_permission`
                                   via `Depends(...)` en sus decoradores de ruta.

Flujo de autenticación por petición:
    1. FastAPI extrae el Bearer token de la cabecera `Authorization` (_bearer).
    2. `get_current_user` verifica firma, expiración y blocklist → `RequestUser`.
    3. `require_permission` (si aplica) comprueba permisos del grupo del usuario,
       cargándolos de BD en la primera comprobación de la petición (lazy load).
    4. El endpoint recibe el `RequestUser` ya validado y autorizado.
"""

import logging
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.security import decode_token
from core.token_blocklist import is_revoked

log = logging.getLogger("api.auth")

# `auto_error=False` es deliberado: impide que FastAPI lance automáticamente un
# 401 cuando la cabecera Authorization está ausente. En su lugar, `credentials`
# llega como `None` a `get_current_user`, que construye un mensaje de error más
# descriptivo. Sin este flag, FastAPI devolvería un 403 con el mensaje genérico
# de Starlette en lugar del 401 esperado por los clientes Flutter y Swing.
_bearer = HTTPBearer(auto_error=False)


# ─────────────────────────────────────────────────────────────────────────────
# Modelos internos de sesión de request
# ─────────────────────────────────────────────────────────────────────────────

class RequestUser:
    """
    Representa la identidad y permisos del usuario autenticado durante el ciclo
    de vida de una única petición HTTP.

    Se construye a partir del payload decodificado del JWT y actúa como
    contenedor de estado ligero que viaja a través de la cadena de dependencias
    FastAPI. No es un modelo Pydantic: se diseñó como clase simple porque no
    necesita serialización ni validación de esquema.

    Patrón de carga de permisos (lazy loading):
        Los campos `_global_perms` y `_section_perms` se inicializan a `None` y
        solo se pueblan la primera vez que se invoca una comprobación de permisos
        en la misma petición (`_load_perms_if_needed`). Esto evita la consulta a
        BD en endpoints que no requieren comprobación de permisos (p. ej., rutas
        públicas que solo usan `get_current_user` sin `require_permission`).

        La distinción entre `None` (no cargado) y `[]` (cargado, sin permisos)
        es fundamental para el funcionamiento del guard en `_load_perms_if_needed`.

    Relación con el JWT:
        Los campos se extraen directamente de las claims del token:
        - `sub`        → username (estándar JWT RFC 7519)
        - `grupoId`    → ID del grupo en la BD relacional
        - `superadmin` → flag de acceso irrestricto
        - `jti`        → JWT ID, usado para la blocklist de logout (RFC 7519)

    Atributos:
        username      (str):                  Nombre del usuario autenticado.
        grupo_id      (int | None):           ID del grupo asignado, o None si
                                              el usuario no pertenece a ningún grupo.
        superadmin    (bool):                 True si el usuario tiene acceso
                                              irrestricto a todos los endpoints.
        _jti          (str | None):           Identificador único del token,
                                              usado en la blocklist de revocación.
        _global_perms (list[str] | None):     Permisos que aplican a todas las
                                              secciones. None = aún no cargados.
        _section_perms(dict[int,list[str]]|None): Permisos indexados por ID de
                                              sección. None = aún no cargados.
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        """
        Constructor de la clase.

        Se ejecuta automáticamente cuando se crea una instancia de esta clase.
        Recibe un diccionario llamado `payload`, que contiene los datos
        decodificados de un token JWT, como el nombre de usuario, el grupo, si es
        superadministrador y el identificador único del token.
        """

        # Obtiene el nombre de usuario desde el campo "sub" del payload.
        # Si la clave "sub" no existe, se producirá un error KeyError.
        self.username: str = payload["sub"]

        # Obtiene el identificador del grupo del usuario desde el payload.
        # Se usa .get() porque este campo puede no existir.
        # Si "grupoId" no está presente, se guardará None.
        self.grupo_id: int | None = payload.get("grupoId")

        # Obtiene si el usuario es superadministrador.
        # Si el campo "superadmin" no existe en el payload, se toma False por defecto.
        # La función bool() convierte el valor recibido a True o False.
        self.superadmin: bool = bool(payload.get("superadmin", False))

        # Obtiene el identificador único del token JWT.
        # Si no existe, se guardará None.
        self._jti: str | None = payload.get("jti")

        # Inicializa la lista de permisos globales como None.
        # Esto indica que todavía no se han cargado desde la base de datos.
        self._global_perms: list[str] | None = None

        # Inicializa los permisos por sección como None.
        # Será un diccionario donde la clave será el id de una sección
        # y el valor será una lista de permisos asociados a esa sección.
        # Por ejemplo:
        # {
        #     1: ["ver", "editar"],
        #     2: ["ver"]
        # }
        # Igual que los permisos globales, se cargarán bajo demanda.
        self._section_perms: dict[int, list[str]] | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Dependencia: usuario autenticado (equivalente a TokenFilter)
# ─────────────────────────────────────────────────────────────────────────────

def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> RequestUser:
    """
    Dependencia FastAPI que valida el JWT y devuelve el usuario autenticado.

    Esta función es el equivalente al `TokenFilter` del backend Java/JAX-RS.
    Se declara como dependencia en los endpoints que requieren autenticación:

        @router.get("/ruta")
        def endpoint(user: Annotated[RequestUser, Depends(get_current_user)]):
            ...

    Proceso de validación (tres capas):
        1. Presencia: comprueba que la cabecera Authorization contiene un
           Bearer token. Si `credentials` es None (auto_error=False en _bearer),
           lanza 401 inmediatamente.
        2. Integridad y vigencia: `decode_token` verifica la firma HMAC-SHA256
           con `jwt_secret` y que el token no ha expirado. Devuelve None si
           cualquiera de estas comprobaciones falla.
        3. Revocación: consulta la blocklist Redis por el JTI del token. Cubre
           el caso de logout explícito, ya que un JWT válido podría seguir siendo
           aceptado por las capas 1 y 2 hasta su expiración natural.

    Seguridad:
        La seguridad de esta función depende completamente de que `jwt_secret`
        sea una clave fuerte y privada (validada en `core/config.py` al arrancar).
        Si el secreto se compromete, la capa 2 queda inutilizada.

    Args:
        credentials: Token Bearer extraído por `_bearer` de la cabecera
                     `Authorization`. `None` si la cabecera está ausente.

    Retorna:
        RequestUser con los datos del usuario autenticado y los permisos pendientes
        de carga (lazy).

    Lanza:
        HTTPException 401: Si el token está ausente, tiene firma inválida,
                           ha expirado o ha sido revocado. Los tres casos devuelven
                           el mismo código 401 para no revelar al cliente cuál de
                           las comprobaciones falló (security through obscurity mínimo).
    """
    if credentials is None:
        log.debug("AUTH token_check: sin token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token no proporcionado",
        )

    payload = decode_token(credentials.credentials)
    if payload is None:
        log.debug("AUTH token_check: token inválido o expirado")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado",
        )

    jti = payload.get("jti")
    if jti and is_revoked(jti):
        log.debug("AUTH token_check: token revocado jti=%s user=%s", jti, payload.get("sub"))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token revocado",
        )

    log.debug("AUTH token_ok jti=%s user=%s grupo_id=%s superadmin=%s",
              jti, payload.get("sub"), payload.get("grupoId"), payload.get("superadmin"))
    return RequestUser(payload)


# ─────────────────────────────────────────────────────────────────────────────
# Factory: require_permission (equivalente a @RequiresPermission + AuthorizationFilter)
# ─────────────────────────────────────────────────────────────────────────────

def require_permission(*required: str):
    """
    Factory que genera una dependencia FastAPI parametrizada por los permisos
    necesarios para acceder a un endpoint.

    Es el equivalente a la combinación de `@RequiresPermission` y
    `AuthorizationFilter` del backend Java/JAX-RS.

    Patrón factory:
        Esta función no es en sí misma una dependencia: es una función que
        *devuelve* una dependencia (`_check`). Esto permite parametrizar la
        comprobación de permisos por ruta sin duplicar código:

            # Solo autenticado (sin permiso específico):
            Depends(require_permission())

            # Requiere al menos uno de los permisos indicados:
            Depends(require_permission("AUDIT_SERV"))
            Depends(require_permission("EDIT_GRUPO", "ADMIN_GRUPO"))

        FastAPI resuelve `_check` como dependencia, inyectando a su vez
        `get_current_user` y `Request` en ella.

    Lógica de autorización (por orden de precedencia):
        1. Superadmin: acceso irrestricto sin ninguna comprobación adicional.
        2. Sin permisos requeridos (`required` vacío): basta con estar autenticado.
        3. Permiso global: el usuario tiene el permiso aplicable a todas las
           secciones → acceso concedido.
        4. Permiso por sección: el usuario tiene el permiso en al menos una sección
           → acceso concedido (el endpoint decide qué secciones mostrar via
           `visible_section_ids`).
        5. Ninguna de las anteriores → 403 Forbidden.

    La carga de permisos desde BD se delega a `_load_perms_if_needed`, que
    garantiza que la consulta solo se realiza una vez por petición.

    Args:
        *required: Nombres de permisos (strings) de los que el usuario debe
                   tener al menos uno. Si se omiten, solo se exige autenticación.

    Retorna:
        Función `_check` usable como dependencia FastAPI que, tras resolver la
        autorización, devuelve el `RequestUser` para que el endpoint lo use.

    Lanza (a través de `_check`):
        HTTPException 401: Si el token no es válido (delegado a `get_current_user`).
        HTTPException 403: Si el usuario autenticado no posee ninguno de los
                           permisos requeridos.
    """

    def _check(
        user: Annotated[RequestUser, Depends(get_current_user)],
        request: Request,
    ) -> RequestUser:
        # Los superadmins tienen acceso irrestricto; no se consulta la BD de permisos.
        if user.superadmin:
            log.debug("AUTHZ user=%s superadmin=True → ALLOWED (sin comprobación de permisos)", user.username)
            return user

        # Sin permisos requeridos: la ruta solo exige estar autenticado.
        if not required:
            log.debug("AUTHZ user=%s sin permisos requeridos → ALLOWED (sólo autenticado)", user.username)
            return user

        # Carga permisos de BD si todavía no se han cargado en esta petición.
        _load_perms_if_needed(user, request)

        global_perms: list[str] = user._global_perms or []
        section_perms: dict[int, list[str]] = user._section_perms or {}

        # Comprobar permisos globales (aplican a todas las secciones).
        for perm in required:
            if perm in global_perms:
                log.debug("AUTHZ user=%s perm=%s → ALLOWED (global)", user.username, perm)
                return user

        # Comprobar permisos por sección (aplican solo a secciones específicas).
        # Si el usuario tiene el permiso en al menos una sección, se concede el
        # acceso al endpoint. El endpoint usa `visible_section_ids` para determinar
        # qué datos concretos puede ver.
        for sec_id, section_list in section_perms.items():
            for perm in required:
                if perm in section_list:
                    log.debug("AUTHZ user=%s perm=%s seccion=%s → ALLOWED (sección)", user.username, perm, sec_id)
                    return user

        log.debug("AUTHZ user=%s required=%s global=%s secciones=%s → DENIED",
                  user.username, required, global_perms, list(section_perms.keys()))
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permiso para realizar esta operación",
        )

    return _check


def visible_section_ids(user: RequestUser, required_perm: str) -> set[int] | None:
    """
    Devuelve el conjunto de IDs de sección accesibles para el usuario dado un
    permiso concreto, o `None` si el usuario tiene acceso irrestricto.

    Esta función complementa a `require_permission`: mientras que este decide si
    el usuario puede acceder al endpoint, `visible_section_ids` determina qué
    subconjunto de datos (secciones/servidores) puede ver dentro del endpoint.
    Es el mecanismo de filtrado de visibilidad que los clientes Flutter y Swing
    usan para mostrar solo los recursos a los que el usuario tiene acceso.

    Precondición:
        Debe llamarse después de que `require_permission` haya resuelto la
        dependencia para el mismo usuario y petición, ya que ese proceso garantiza
        que `_global_perms` y `_section_perms` están cargados. Llamarla antes
        puede producir resultados incorrectos (permisos aún `None`).

    Semántica del valor devuelto:
        - `None`       → acceso irrestricto (superadmin o permiso global).
                         El endpoint debe devolver todos los datos sin filtrar.
        - `set` vacío  → el usuario no tiene acceso a ninguna sección para ese
                         permiso. El endpoint debe devolver una lista vacía.
        - `set` con IDs → el usuario tiene acceso solo a esas secciones.
                          El endpoint debe filtrar los datos a esos IDs.

    Args:
        user:          Usuario autenticado (con permisos ya cargados).
        required_perm: Nombre del permiso cuyas secciones se quieren consultar.

    Retorna:
        `None` para acceso irrestricto, o un `set[int]` con los IDs de sección
        accesibles (potencialmente vacío).
    """
    if user.superadmin:
        return None
    if required_perm in (user._global_perms or []):
        return None
    return {
        sec_id
        for sec_id, perms in (user._section_perms or {}).items()
        if required_perm in perms
    }


def _load_perms_if_needed(user: RequestUser, request: Request) -> None:
    """
    Carga los permisos del grupo del usuario desde MariaDB si aún no han sido
    cargados en el ciclo de vida de la petición actual.

    Implementa el patrón de carga lazy: la condición `user._global_perms is not None`
    actúa como guard. Una vez cargados (incluso si el resultado es una lista vacía),
    las llamadas posteriores dentro de la misma petición son no-ops, evitando
    consultas redundantes a BD cuando múltiples `require_permission` se encadenan.

    Gestión de la sesión de BD:
        Esta función abre su propia sesión directamente desde `engine` en lugar
        de usar la sesión inyectada por `get_session()`. Esto es necesario porque
        `_load_perms_if_needed` no es en sí misma una dependencia FastAPI con
        acceso al grafo de inyección de dependencias; es una función helper
        invocada desde dentro de `_check`. El `with Session(engine)` garantiza
        que la sesión se cierra al terminar la carga, independientemente de errores.

    Los imports de `GrupoRepository`, `engine` y `Session` son diferidos (dentro
    de la función) para romper posibles importaciones circulares con `repositories/`
    y `core/database.py`.

    Comportamiento ante fallos de BD:
        Si la consulta a BD falla por cualquier motivo (BD caída, timeout, error
        de esquema, etc.), se captura la excepción, se loguea un WARNING y se
        asignan permisos vacíos al usuario. Esto implementa un comportamiento
        fail-closed para usuarios sin acceso privilegiado: ante la duda, denegar.
        El usuario verá un 403 en el endpoint en lugar de un 500, lo que puede
        dificultar el diagnóstico si no se monitorizan los logs de WARNING.

    Args:
        user:    Usuario autenticado cuyo grupo_id se usará para cargar permisos.
        request: Petición HTTP activa (recibido como parámetro por coherencia con
                 la firma de `_check`, pero no se usa directamente aquí).
    """
    # Guard: si _global_perms ya tiene valor (lista cargada o lista vacía), no volver a consultar.
    if user._global_perms is not None:
        return
    if user.grupo_id is None:
        log.debug("PERMS user=%s sin grupo → permisos vacíos", user.username)
        user._global_perms = []
        user._section_perms = {}
        return

    from repositories.grupo_repo import GrupoRepository
    from core.database import engine
    from sqlmodel import Session

    try:
        with Session(engine) as session:
            repo = GrupoRepository(session)
            user._global_perms = repo.get_global_permission_names(user.grupo_id)
            user._section_perms = repo.get_section_permission_names(user.grupo_id)
        log.debug("PERMS loaded user=%s grupo_id=%s global=%s secciones=%s",
                  user.username, user.grupo_id,
                  user._global_perms, list(user._section_perms.keys()))
    except Exception as exc:
        log.warning("PERMS error cargando permisos user=%s: %s", user.username, exc)
        user._global_perms = []
        user._section_perms = {}
