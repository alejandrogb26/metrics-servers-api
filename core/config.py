"""
Este archivo contiene la configuración principal de la API, gestionada mediante Pydantic.
Define las configuraciones necesarias para las conexiones a bases de datos, LDAP, MinIO, 
la gestión de JWT, CORS, y Redis. Las configuraciones son cargadas desde un archivo `.env`, 
lo que permite que el entorno sea configurable sin necesidad de modificar el código.

Se utiliza el decorador `@lru_cache` para cachear la configuración, optimizando la carga 
de estas configuraciones a lo largo del ciclo de vida de la aplicación.

Dependencias:
    - Pydantic: Para la validación y gestión de la configuración.
    - Pydantic-Settings: Para cargar la configuración desde variables de entorno.
"""

from functools import lru_cache
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Conjunto de valores inseguros para JWT. Estos deben ser reemplazados en producción.
_INSECURE_JWT_DEFAULTS = {
    "cambia_esto_en_produccion",
    "secret",
    "changeme",
    "jwt_secret",
    "",
}

class Settings(BaseSettings):
    """
    Clase que define la configuración de la aplicación. Hereda de `BaseSettings` de Pydantic
    y carga las variables de entorno desde un archivo `.env` con codificación UTF-8.
    
    Atributos:
        db_host (str): El host de la base de datos MariaDB.
        db_port (int): El puerto de la base de datos MariaDB. Por defecto es 3306.
        db_name (str): El nombre de la base de datos MariaDB.
        db_user (str): El usuario de la base de datos MariaDB.
        db_password (str): La contraseña de la base de datos MariaDB.
        mongo_uri (str): URI de conexión para MongoDB.
        mongo_db (str): Nombre de la base de datos MongoDB.
        ldap_url (str): URL del servidor LDAP.
        ldap_base_dn (str): Base DN para la autenticación LDAP.
        ldap_svc_dn (str): DN de servicio para la autenticación LDAP.
        ldap_svc_pw (str): Contraseña del servicio para la autenticación LDAP.
        minio_endpoint (str): Punto de acceso para MinIO.
        minio_access_key (str): Clave de acceso para MinIO.
        minio_secret_key (str): Clave secreta para MinIO.
        bucket_users (str): Nombre del bucket para almacenar usuarios en MinIO.
        bucket_servidores (str): Nombre del bucket para almacenar servidores en MinIO.
        bucket_servicios (str): Nombre del bucket para almacenar servicios en MinIO.
        jwt_secret (str): Clave secreta para la generación de tokens JWT.
        jwt_expiration_seconds (int): Tiempo de expiración del token JWT en segundos. Por defecto es 28800 (8 horas).
        redis_url (str): URL de conexión para Redis (utilizado para la lista de bloqueo de JWT).
        cors_origins (list): Lista de orígenes permitidos para CORS.
        ssh_probe_user (str): Usuario para realizar la prueba SSH.
        ssh_probe_password (str): Contraseña para realizar la prueba SSH.
    """

    model_config = SettingsConfigDict(
        env_file=".env",  # Carga el archivo .env
        env_file_encoding="utf-8",  # Codificación UTF-8 para el archivo de entorno
        case_sensitive=False,  # No distingue entre mayúsculas y minúsculas
    )

    # ── MariaDB ──────────────────────────────────────────────────────────────
    db_host: str  # Dirección del host de MariaDB.
    db_port: int = 3306  # Puerto para conectarse a MariaDB. Valor por defecto 3306.
    db_name: str  # Nombre de la base de datos.
    db_user: str  # Usuario para autenticar en la base de datos.
    db_password: str  # Contraseña del usuario de la base de datos.

    @property
    def database_url(self) -> str:
        """
        Propiedad que genera la URL de conexión para SQLAlchemy utilizando los detalles
        de configuración proporcionados para MariaDB.

        Retorna:
            str: URL de conexión para la base de datos MariaDB.
        """
        return (
            f"mysql+pymysql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
            "?charset=utf8mb4"
        )

    # ── MongoDB ──────────────────────────────────────────────────────────────
    mongo_uri: str  # URI de conexión para MongoDB.
    mongo_db: str  # Nombre de la base de datos de MongoDB.

    # ── LDAP ─────────────────────────────────────────────────────────────────
    ldap_url: str  # URL del servidor LDAP.
    ldap_base_dn: str  # Base DN para la autenticación LDAP.
    ldap_svc_dn: str  # DN del servicio para autenticación LDAP.
    ldap_svc_pw: str  # Contraseña para el servicio LDAP.

    # ── MinIO ─────────────────────────────────────────────────────────────────
    minio_endpoint: str  # Endpoint de MinIO.
    minio_access_key: str  # Clave de acceso de MinIO.
    minio_secret_key: str  # Clave secreta de MinIO.
    bucket_users: str = "usuarios"  # Nombre del bucket para usuarios.
    bucket_servidores: str = "servidores"  # Nombre del bucket para servidores.
    bucket_servicios: str = "servicios"  # Nombre del bucket para servicios.

    # ── JWT ───────────────────────────────────────────────────────────────────
    jwt_secret: str  # Clave secreta para la firma de tokens JWT.
    jwt_expiration_seconds: int = 28800  # Tiempo de expiración del JWT en segundos (por defecto 8 horas).

    @field_validator("jwt_secret")
    @classmethod
    def jwt_secret_must_not_be_default(cls, v: str) -> str:
        """
        Valida que el valor de `jwt_secret` no sea uno de los valores inseguros predeterminados.
        Si el valor es uno de los valores inseguros, lanza un error de configuración.

        Args:
            v (str): El valor de la clave secreta JWT.

        Retorna:
            str: La clave secreta JWT, si es válida.

        Lanza:
            ValueError: Si el valor de `jwt_secret` es uno de los predeterminados inseguros.
        """
        if v.lower() in _INSECURE_JWT_DEFAULTS:
            raise ValueError(
                "JWT_SECRET no puede ser el valor por defecto. "
                "Configura una clave aleatoria segura en el fichero .env"
            )
        return v

    # ── Redis (blocklist de JWT) ──────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379"  # URL para conectar con Redis, por defecto en localhost:6379.

    # ── CORS ──────────────────────────────────────────────────────────────────
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:8080"]  # Orígenes permitidos para CORS.

    # ── SSH Probe ─────────────────────────────────────────────────────────────
    ssh_probe_user: str  # Usuario para realizar la prueba SSH.
    ssh_probe_password: str  # Contraseña para realizar la prueba SSH.

@lru_cache
def get_settings() -> Settings:
    """
    Función para obtener la configuración de la aplicación, utilizando cacheo LRU
    para evitar recargar la configuración repetidamente.

    Retorna:
        Settings: Instancia de la clase `Settings` con las configuraciones cargadas desde el entorno.
    """
    return Settings()