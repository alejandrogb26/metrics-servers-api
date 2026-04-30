"""
Este archivo contiene funciones para la creación y decodificación de tokens JWT (JSON Web Tokens).
Las funciones permiten generar tokens de acceso para los usuarios y verificar su validez al momento
de ser decodificados. Utiliza la librería **python-jose** para la creación y validación de los tokens.

Dependencias:
    - jose.JWTError, jwt: Para la creación, firma y validación de tokens JWT.
    - core.config.get_settings: Para obtener la configuración relacionada con JWT.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt

from core.config import get_settings

# Algoritmo de firma para el JWT
ALGORITHM = "HS256"


def create_access_token(
    username: str,
    display_name: str | None,
    mail: str | None,
    grupo_id: int,
    superadmin: bool,
) -> str:
    """
    Crea un token JWT de acceso para el usuario autenticado.

    Este token contiene información sobre el usuario y sus permisos, y tiene una expiración
    basada en la configuración definida en el archivo `.env` (ajustada en `jwt_expiration_seconds`).

    Parámetros:
        username (str): El nombre de usuario del usuario autenticado.
        display_name (str | None): El nombre para mostrar del usuario.
        mail (str | None): El correo electrónico del usuario.
        grupo_id (int): El ID del grupo al que pertenece el usuario.
        superadmin (bool): Indica si el usuario es un superadministrador.

    Retorna:
        str: El token JWT firmado.

    Proceso:
        1. Se obtiene la configuración de JWT desde el archivo `.env`.
        2. Se calcula la fecha de expiración del token.
        3. Se genera un payload con la información del usuario y se firma con la clave secreta.
    """
    settings = get_settings()  # Obtener la configuración de JWT desde el archivo de configuración
    now = datetime.now(tz=timezone.utc)  # Hora actual en UTC
    expire = now + timedelta(seconds=settings.jwt_expiration_seconds)  # Calcular la fecha de expiración

    # Crear el payload del token con la información del usuario
    payload: dict[str, Any] = {
        "jti": str(uuid.uuid4()),  # Identificador único del token (JTI)
        "sub": username,  # Nombre de usuario (sub es el "subject" en JWT)
        "username": username,  # Nombre de usuario
        "displayName": display_name,  # Nombre para mostrar del usuario
        "mail": mail,  # Correo electrónico del usuario
        "grupoId": grupo_id,  # ID del grupo
        "superadmin": superadmin,  # Si el usuario es superadministrador
        "iat": now,  # Tiempo de emisión del token
        "exp": expire,  # Tiempo de expiración del token
    }

    # Firmar el token con la clave secreta y el algoritmo especificado
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_token(token: str) -> dict[str, Any] | None:
    """
    Decodifica un token JWT y retorna su payload.

    Si el token es inválido o ha expirado, devuelve `None`.

    Parámetros:
        token (str): El token JWT a decodificar.

    Retorna:
        dict[str, Any] | None: El payload del token si es válido, o `None` si es inválido o expirado.

    Excepciones:
        JWTError: Si el token no es válido o ha expirado.
    """
    settings = get_settings()  # Obtener la configuración de JWT desde el archivo de configuración
    try:
        # Decodificar el token usando la clave secreta y el algoritmo especificado
        return jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except JWTError:
        # Si el token es inválido o ha expirado, retornar None
        return None