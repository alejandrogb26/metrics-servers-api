"""
Módulo de configuración central de la aplicación.

Capa arquitectónica: Infraestructura / Configuración.

Responsabilidades:
    - Definir y validar todos los parámetros de configuración de la aplicación,
      leyéndolos desde el fichero `.env` (o variables de entorno del sistema).
    - Proporcionar una única instancia cacheada de la configuración (`get_settings`)
      que el resto de módulos consumen a través de la inyección de dependencias de
      FastAPI (ver `core/dependencies.py`).
    - Aplicar validaciones de seguridad tempranas (p. ej., rechazar claves JWT débiles)
      para que la aplicación falle al arrancar en lugar de hacerlo en producción bajo
      carga.

Qué NO debe contener este fichero:
    - Lógica de negocio de ningún tipo.
    - Inicialización de clientes externos (conexiones a BD, MinIO, Redis...).
      Eso pertenece a `core/dependencies.py`.
    - Configuración de rutas HTTP o middlewares. Eso pertenece a `main.py`.

Patrón de diseño:
    Se usa `pydantic_settings.BaseSettings` para aprovechar la validación de tipos
    de Pydantic sobre los valores cargados desde el entorno. Combinado con
    `@lru_cache`, garantiza que el fichero `.env` se parsea exactamente una vez
    durante el ciclo de vida del proceso, evitando lecturas redundantes de disco y
    asegurando que todos los módulos trabajan con la misma instancia de configuración.

Dependencias externas:
    - pydantic / pydantic-settings: Validación y carga de configuración.
    - python-dotenv (transitivo via pydantic-settings): Lectura del fichero `.env`.
"""

from functools import lru_cache
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Conjunto de valores que se consideran inseguros para `jwt_secret`.
# Este conjunto actúa como lista negra explícita: si el operador despliega la
# aplicación sin cambiar el secreto JWT del fichero `.env.example`, el arranque
# fallará inmediatamente con un error descriptivo, evitando que tokens firmados con
# claves triviales lleguen a producción.
# La comparación se hace en minúsculas (ver validador `jwt_secret_must_not_be_default`)
# para capturar variantes como "SECRET", "Secret", etc.
_INSECURE_JWT_DEFAULTS = {
    "cambia_esto_en_produccion",
    "secret",
    "changeme",
    "jwt_secret",
    "",
}

class Settings(BaseSettings):
    """
    Clase de configuración global de la aplicación.

    Hereda de `pydantic_settings.BaseSettings`, lo que permite que cada atributo
    sea poblado automáticamente desde:
      1. Variables de entorno del sistema operativo.
      2. El fichero `.env` en el directorio de trabajo (prioridad más baja).

    El orden de precedencia es el estándar de pydantic-settings: variables de entorno
    del sistema tienen prioridad sobre el fichero `.env`.

    Relaciones con otros módulos:
        - `core/dependencies.py` importa `get_settings()` para construir los clientes
          de BD, MinIO, Redis y el verificador JWT.
        - `main.py` consume `get_settings()` para configurar CORS y el modo debug.
        - `services/ldap_service.py` usa los campos `ldap_*` para conectar al
          directorio corporativo.
        - `services/minio_service.py` usa los campos `minio_*` y `bucket_*`.
        - `routers/auth.py` y `services/auth_service.py` usan `jwt_secret` y
          `jwt_expiration_seconds` para emitir y verificar tokens.

    Detalles de diseño:
        - Todos los campos sin valor por defecto son **obligatorios**: si falta alguno
          en el entorno, Pydantic lanzará un `ValidationError` al arrancar.
        - `case_sensitive=False` permite que las variables de entorno estén definidas
          en mayúsculas (`DB_HOST`) o minúsculas (`db_host`) indistintamente, lo que
          facilita la compatibilidad con convenciones distintas (Docker Compose suele
          usar mayúsculas; el fichero `.env` suele usar minúsculas).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # `case_sensitive=False` es importante para compatibilidad con entornos donde
        # las variables se declaran en mayúsculas (p. ej., Docker Compose, Kubernetes
        # ConfigMaps) sin tener que duplicar la definición.
        case_sensitive=False,
    )

    # ── MariaDB ──────────────────────────────────────────────────────────────
    # Credenciales y localización del servidor MariaDB que aloja el esquema
    # relacional principal (usuarios, servidores, grupos, servicios).
    # La URL de conexión completa se construye dinámicamente en `database_url`.
    db_host: str
    db_port: int = 3306
    db_name: str
    db_user: str
    db_password: str

    @property
    def database_url(self) -> str:
        """
        Construye y devuelve la URL de conexión SQLAlchemy para MariaDB.

        Utiliza el driver `pymysql` (puro Python), que no requiere librerías C
        nativas, simplificando el despliegue en contenedores.

        El parámetro `charset=utf8mb4` es deliberado: `utf8` en MySQL/MariaDB es
        en realidad una codificación de 3 bytes que no puede representar caracteres
        fuera del BMP (p. ej., emojis). `utf8mb4` es el UTF-8 real de 4 bytes y es
        necesario para almacenar correctamente cualquier texto Unicode moderno.

        Retorna:
            str: URL con formato `mysql+pymysql://user:password@host:port/db?charset=utf8mb4`.

        Nota de seguridad:
            La contraseña se incrusta directamente en la URL. Si `db_password`
            contiene caracteres especiales como `@`, `/` o `?`, la URL resultante
            puede ser inválida o ambigua. En ese caso habría que aplicar
            `urllib.parse.quote_plus` sobre la contraseña antes de concatenarla.
        """
        return (
            f"mysql+pymysql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
            "?charset=utf8mb4"
        )

    # ── MongoDB ──────────────────────────────────────────────────────────────
    # MongoDB se usa para almacenar datos no relacionales de monitorización
    # (métricas, logs de servidores, etc.). Ver `repositories/mongo_repo.py`.
    mongo_uri: str
    mongo_db: str

    # ── LDAP ─────────────────────────────────────────────────────────────────
    # El directorio LDAP corporativo es el proveedor de identidad principal.
    # La autenticación de usuarios se delega completamente a LDAP: esta API
    # no almacena contraseñas de usuarios, solo valida credenciales contra el
    # directorio en tiempo real (ver `services/ldap_service.py`).
    #
    # `ldap_svc_dn` y `ldap_svc_pw` son las credenciales de una cuenta de
    # servicio con permisos de lectura sobre el directorio, necesaria para
    # hacer búsquedas de usuarios antes del bind de autenticación.
    ldap_url: str
    ldap_base_dn: str
    ldap_svc_dn: str
    ldap_svc_pw: str

    # ── MinIO ─────────────────────────────────────────────────────────────────
    # MinIO actúa como almacén de objetos S3-compatible para ficheros binarios
    # (avatares de usuario, capturas de servidores, iconos de servicios).
    # Se divide en tres buckets lógicos para separar los distintos dominios de
    # datos y facilitar políticas de retención o acceso independientes.
    # Ver `services/minio_service.py` para la implementación del cliente.
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    bucket_users: str = "usuarios"
    bucket_servidores: str = "servidores"
    bucket_servicios: str = "servicios"

    # ── JWT ───────────────────────────────────────────────────────────────────
    # `jwt_secret` es la clave HMAC con la que se firman todos los tokens JWT
    # emitidos por esta API. Su compromiso equivale a comprometer la autenticación
    # completa del sistema: cualquier atacante que la conozca puede forjar tokens
    # válidos para cualquier usuario.
    #
    # `jwt_expiration_seconds` define la ventana de validez de un token. El valor
    # por defecto de 28800 s (8 horas) cubre una jornada laboral completa sin
    # forzar re-autenticaciones. Para entornos de mayor seguridad se recomienda
    # reducirlo y combinar con un mecanismo de refresh token (no implementado aún).
    #
    # IMPORTANTE: El validador `jwt_secret_must_not_be_default` impide arrancar
    # con valores triviales. Ver su docstring para detalles.
    jwt_secret: str
    jwt_expiration_seconds: int = 28800

    @field_validator("jwt_secret")
    @classmethod
    def jwt_secret_must_not_be_default(cls, v: str) -> str:
        """
        Validador Pydantic que rechaza valores triviales o de ejemplo en `jwt_secret`.

        Se ejecuta en el momento de instanciar `Settings`, es decir, al arrancar la
        aplicación. Esto implementa el principio de "fallo rápido": es preferible que
        el proceso no arranque a que lo haga con una configuración de seguridad
        deficiente.

        La comparación se normaliza a minúsculas para capturar variantes como
        "SECRET", "Secret", "CHANGEME", etc., que seguirían siendo inseguras aunque
        no coincidan literalmente con las entradas del conjunto `_INSECURE_JWT_DEFAULTS`.

        Args:
            v (str): Valor leído del entorno para el campo `jwt_secret`.

        Retorna:
            str: El mismo valor `v` sin modificar, si supera la validación.

        Lanza:
            ValueError: Si `v` (en minúsculas) pertenece a `_INSECURE_JWT_DEFAULTS`.
                        Pydantic lo envuelve en un `ValidationError` que interrumpe
                        el arranque de la aplicación con un mensaje de error claro.
        """
        if v.lower() in _INSECURE_JWT_DEFAULTS:
            raise ValueError(
                "JWT_SECRET no puede ser el valor por defecto. "
                "Configura una clave aleatoria segura en el fichero .env"
            )
        return v

    # ── Redis (blocklist de JWT) ──────────────────────────────────────────────
    # Redis se usa exclusivamente como almacén de la blocklist de tokens JWT
    # revocados. Los tokens JWT son por naturaleza stateless: una vez emitidos,
    # son válidos hasta su expiración aunque el usuario haga logout. La blocklist
    # en Redis corrige este comportamiento almacenando los JTI (JWT ID) de los
    # tokens invalidados con un TTL igual a su tiempo de expiración residual.
    #
    # El valor por defecto apunta a un Redis local sin autenticación, adecuado
    # solo para desarrollo. En producción debe configurarse explícitamente con
    # credenciales y TLS (p. ej., `redis://:password@redis-host:6379/0`).
    redis_url: str = "redis://localhost:6379"

    # ── CORS ──────────────────────────────────────────────────────────────────
    # Lista de orígenes permitidos para peticiones cross-origin. El middleware
    # CORS de FastAPI (configurado en `main.py`) usa esta lista para añadir las
    # cabeceras `Access-Control-Allow-Origin` apropiadas.
    #
    # Los valores por defecto solo son válidos para desarrollo local (cliente
    # Flutter web en puerto 3000, cliente Swing en 8080). En producción esta
    # lista debe contener exclusivamente los dominios reales de los clientes.
    #
    # Nota: el cliente Flutter móvil no envía cabeceras CORS (no usa un navegador),
    # por lo que no está restringido por esta configuración. El cliente Java Swing
    # tampoco usa CORS. Solo afecta al cliente Flutter web.
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:8080"]

    # ── SSH Probe ─────────────────────────────────────────────────────────────
    # Credenciales de una cuenta técnica con acceso SSH a los servidores
    # monitorizados. Se usan para verificar conectividad SSH como parte de las
    # pruebas de disponibilidad de servidores (ver `services/servidor_service.py`).
    #
    # Limitación conocida: estas credenciales son globales para todos los servidores.
    # Si los servidores gestionados tienen distintas credenciales SSH, habría que
    # modelarlas por servidor en la base de datos en lugar de aquí.
    ssh_probe_user: str
    ssh_probe_password: str

    # ── Debug ─────────────────────────────────────────────────────────────────
    # Cuando es `True`, activa logging HTTP detallado (cabeceras, cuerpos de
    # petición/respuesta), stacktraces completos en respuestas de error y nivel
    # DEBUG en todas las capas internas (ver `core/debug_middleware.py`).
    #
    # ADVERTENCIA DE SEGURIDAD: Nunca establecer a `True` en producción. Los logs
    # detallados pueden exponer tokens, contraseñas u otros datos sensibles presentes
    # en cabeceras o cuerpos de petición.
    app_debug: bool = False

@lru_cache
def get_settings() -> Settings:
    """
    Devuelve la instancia única de configuración de la aplicación.

    El decorador `@lru_cache` garantiza que `Settings()` se construye —y por tanto
    el fichero `.env` se lee y valida— exactamente una vez durante el ciclo de vida
    del proceso, independientemente de cuántos módulos llamen a esta función.

    Uso desde FastAPI mediante inyección de dependencias:

        from fastapi import Depends
        from core.config import get_settings, Settings

        @router.get("/ruta")
        def mi_endpoint(settings: Settings = Depends(get_settings)):
            ...

    O directamente en código no-endpoint (inicialización de clientes, etc.):

        settings = get_settings()

    Retorna:
        Settings: Instancia cacheada de la configuración. Todos los campos han sido
                  validados por Pydantic en el momento de la primera llamada.

    Lanza:
        pydantic.ValidationError: En la primera llamada, si algún campo obligatorio
            falta en el entorno o si algún validador (como `jwt_secret_must_not_be_default`)
            rechaza el valor configurado. El proceso no debería continuar en este caso.
    """
    return Settings()
