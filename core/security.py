"""
Módulo de creación y verificación de tokens JWT.

Capa arquitectónica: Infraestructura / Seguridad transversal.

Responsabilidades:
    - Emitir tokens JWT firmados con HMAC-SHA256 para los usuarios que se
      autentican correctamente (`create_access_token`).
    - Verificar la firma y la vigencia de los tokens recibidos en peticiones
      entrantes, devolviendo el payload decodificado o `None` si el token no
      es válido (`decode_token`).

Qué NO debe contener este fichero:
    - Lógica de autenticación (verificación de credenciales LDAP). Eso
      pertenece a `services/auth_service.py`.
    - Comprobación de la blocklist de revocación. Eso pertenece a
      `core/token_blocklist.py` y se invoca desde `core/dependencies.py`.
    - Lógica de autorización (comprobación de permisos). Eso pertenece a
      `core/dependencies.py`.

Relaciones con otros módulos:
    - `core/config.py`         → proporciona `jwt_secret` (clave de firma HMAC)
                                 y `jwt_expiration_seconds` (ventana de validez).
    - `services/auth_service.py` → llama a `create_access_token` tras verificar
                                 las credenciales del usuario contra LDAP.
    - `core/dependencies.py`   → llama a `decode_token` en cada petición protegida
                                 para validar el Bearer token entrante.
    - `core/debug_middleware.py` → llama a `decode_token` para extraer el
                                 username del token con fines de logging.

Algoritmo de firma:
    Se usa HMAC-SHA256 (`HS256`), un algoritmo simétrico: la misma clave secreta
    (`jwt_secret`) firma y verifica los tokens. Esto es apropiado para una API
    monolítica donde el emisor y el verificador son el mismo servicio. Si en el
    futuro se necesitara que servicios externos verifiquen tokens sin conocer el
    secreto, habría que migrar a un algoritmo asimétrico (RS256 o ES256).

Compatibilidad con clientes:
    El payload del token es consumido directamente por los clientes Flutter y
    Java Swing, que leen claims como `displayName`, `mail` y `grupoId` para
    personalizar la interfaz. Cambiar el nombre de cualquier claim rompería
    la compatibilidad con esos clientes sin una migración coordinada.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt

from core.config import get_settings

# Algoritmo de firma HMAC-SHA256. Se define como constante para garantizar
# coherencia entre `create_access_token` (firma) y `decode_token` (verificación).
# En `decode_token` se pasa como lista (`algorithms=[ALGORITHM]`) para prevenir
# ataques de confusión de algoritmo: si se aceptara cualquier algoritmo, un
# atacante podría intentar forjar tokens usando el algoritmo "none".
ALGORITHM = "HS256"


def create_access_token(
    username: str,
    display_name: str | None,
    mail: str | None,
    grupo_id: int,
    superadmin: bool,
) -> str:
    """
    Genera y firma un token JWT de acceso para el usuario autenticado.

    El token resultante es autocontenido: incluye toda la información necesaria
    para identificar al usuario y determinar su nivel de acceso sin consultar
    la base de datos en cada petición. Esta es la propiedad stateless de JWT.

    Estructura del payload:
        Claims estándar (RFC 7519):
            - `jti`  (str):  JWT ID. UUID4 único por token. Es la clave que
                             permite revocar tokens individuales en la blocklist
                             Redis (`core/token_blocklist.py`). Sin `jti` no
                             sería posible invalidar un token concreto sin
                             invalidar todos los del usuario.
            - `sub`  (str):  Subject. Nombre de usuario. Campo estándar usado
                             por `core/dependencies.py` para identificar al usuario.
            - `iat`  (datetime): Issued At. Instante de emisión en UTC.
            - `exp`  (datetime): Expiration Time. Instante de expiración en UTC,
                             calculado como `iat + jwt_expiration_seconds`.

        Claims personalizados (consumidos por clientes Flutter y Swing):
            - `username`    (str):      Redundante con `sub`. Presente por
                                        compatibilidad con clientes que esperan
                                        explícitamente un campo `username`.
            - `displayName` (str|None): Nombre visible del usuario, obtenido de
                                        LDAP. Los clientes lo muestran en la UI.
            - `mail`        (str|None): Correo electrónico del usuario desde LDAP.
            - `grupoId`     (int):      ID del grupo en la BD relacional. Usado
                                        por `core/dependencies.py` para cargar
                                        los permisos del grupo bajo demanda.
            - `superadmin`  (bool):     Flag de acceso irrestricto. `True` omite
                                        todas las comprobaciones de permiso en
                                        `core/dependencies.py`. Su integridad
                                        depende exclusivamente de que `jwt_secret`
                                        no esté comprometido.

    Todos los timestamps se generan en UTC (`timezone.utc`). Usar la hora local
    del servidor causaría tokens con tiempos de expiración incorrectos en
    entornos con zonas horarias distintas o cambios de horario (DST).

    Args:
        username:     Nombre de usuario (login) del usuario autenticado.
        display_name: Nombre legible para mostrar en la UI. Puede ser None si
                      el atributo no está disponible en el directorio LDAP.
        mail:         Correo electrónico del usuario desde LDAP. Puede ser None.
        grupo_id:     ID del grupo asignado al usuario en MariaDB.
        superadmin:   True si el usuario debe tener acceso irrestricto.

    Retorna:
        str: Token JWT firmado con HMAC-SHA256, listo para enviarse al cliente
             en la respuesta de login. El cliente debe incluirlo en las
             peticiones posteriores como `Authorization: Bearer <token>`.
    """
    settings = get_settings()
    now = datetime.now(tz=timezone.utc)
    expire = now + timedelta(seconds=settings.jwt_expiration_seconds)

    payload: dict[str, Any] = {
        "jti": str(uuid.uuid4()),
        "sub": username,
        "username": username,
        "displayName": display_name,
        "mail": mail,
        "grupoId": grupo_id,
        "superadmin": superadmin,
        "iat": now,
        "exp": expire,
    }

    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_token(token: str) -> dict[str, Any] | None:
    """
    Verifica la firma y la vigencia de un token JWT y devuelve su payload.

    Realiza dos comprobaciones en una sola operación:
        1. Firma HMAC-SHA256: verifica que el token fue emitido por esta API
           con `jwt_secret`. Un token manipulado o firmado con otra clave falla
           aquí.
        2. Expiración (`exp`): verifica que el instante actual es anterior al
           tiempo de expiración del token. Un token expirado falla aquí aunque
           la firma sea válida.

    Política de error silencioso:
        Cualquier `JWTError` (firma inválida, token expirado, token malformado,
        algoritmo no permitido, etc.) se captura y se devuelve `None` en su
        lugar. Esta decisión de diseño es deliberada:
          - El llamante (`core/dependencies.py`) solo necesita saber si el token
            es válido o no; los detalles del fallo no son relevantes para él.
          - Evita que el tipo exacto de error JWT se propague y quede expuesto
            en respuestas HTTP o logs sin sanitizar.
          - Simplifica la lógica del llamante, que solo comprueba `if payload is None`.
        La contrapartida es que, durante el diagnóstico, un token que falla no
        indica si es por expiración, firma incorrecta o malformación.

    El parámetro `algorithms=[ALGORITHM]` es una lista explícita de un solo
    elemento, no una cadena. Esto es un requisito de seguridad de python-jose:
    aceptar cualquier algoritmo permitiría ataques de confusión donde un atacante
    cambia la cabecera del token a `{"alg": "none"}` para saltarse la verificación
    de firma.

    Args:
        token: Cadena JWT tal como se recibe en la cabecera `Authorization`,
               sin el prefijo "Bearer ".

    Retorna:
        dict[str, Any]: Payload decodificado del token si la firma es válida y
                        el token no ha expirado. Contiene todas las claims
                        definidas en `create_access_token`.
        None: Si el token tiene firma inválida, está expirado, está malformado
              o usa un algoritmo no permitido.
    """
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except JWTError:
        return None
