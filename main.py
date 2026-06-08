"""
metrics-servers — API de monitorización de servidores
Python 3.12+ · FastAPI · SQLModel · MariaDB · MongoDB · MinIO · LDAP

Capa arquitectónica: Punto de entrada / Composición de la aplicación.

Responsabilidades:
    - Inicializar el sistema de logging antes de que cualquier otro módulo lo use.
    - Definir el ciclo de vida de la aplicación (startup/shutdown) mediante el
      patrón `lifespan` de FastAPI.
    - Instanciar la aplicación FastAPI con su título, descripción y versión.
    - Registrar middleware en el orden correcto (logging externo → CORS interno).
    - Calcular dinámicamente `allow_credentials` para cumplir la spec CORS.
    - Registrar los manejadores de excepción globales.
    - Incluir todos los routers de la API.
    - Proporcionar un punto de entrada directo para desarrollo con Uvicorn.

Qué NO debe contener este fichero:
    - Lógica de negocio ni acceso a datos. Eso pertenece a los servicios y
      repositorios.
    - Definición de rutas. Eso pertenece a los módulos en `routers/`.
    - Definición de modelos ni esquemas. Eso pertenece a `models/`.

Orden de inicialización (relevante para diagnosticar problemas de arranque):
    1. `get_settings()` + `setup_logging()` — configuración y logging, a nivel
       de módulo, se ejecutan en el momento de importar `main.py`.
    2. `lifespan` — startup: prueba de conexión MariaDB (no fatal).
    3. `FastAPI(lifespan=lifespan)` — crea la instancia de la app.
    4. `add_middleware(DebugLoggingMiddleware)` — middleware de logging HTTP,
       más externo (procesado primero en la request).
    5. `add_middleware(CORSMiddleware)` — middleware CORS, más interno.
    6. `register_exception_handlers(app)` — manejadores globales de error.
    7. `app.include_router(…)` × 10 — registro de todos los routers.

Relaciones con otros módulos:
    - `core/config.py`           → `get_settings` para configuración de la app.
    - `core/logging_config.py`   → `setup_logging` para configurar el sistema
                                   de logging antes de crear la app.
    - `core/debug_middleware.py` → `DebugLoggingMiddleware` para logar requests
                                   HTTP con duración.
    - `core/database.py`         → `engine` para la prueba de conexión a MariaDB
                                   en el startup (import lazy dentro de lifespan).
    - `core/mongo.py`            → `get_mongo_client` para cerrar la conexión
                                   MongoDB en el shutdown (import lazy).
    - `exceptions/handlers.py`  → `register_exception_handlers` para los
                                   manejadores globales de error.
    - `routers/`                 → 10 routers incluidos en la app.

Autor:
    Alejandro Gómez Blanco

Proyecto:
    Metrics Servers

Versión:
    1.0.0

Organización:
    Metrics Servers Project
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import get_settings
from core.logging_config import setup_logging
from core.project_info import (
    PROJECT_AUTHOR,
    PROJECT_AUTHOR_EMAIL,
    PROJECT_COMPANY,
    PROJECT_DESCRIPTION,
    PROJECT_LICENSE,
    PROJECT_NAME,
    PROJECT_URL,
    PROJECT_VERSION,
)
from exceptions.handlers import register_exception_handlers
from routers import (
    ambito,
    auth,
    grupo,
    grupo_permisos,
    health,
    info,
    permiso,
    seccion,
    servidor,
    servicio,
    usuario,
)

# ── Logging ───────────────────────────────────────────────────────────────────
# Se inicializa antes de crear la app para que el lifespan ya use el logger.

_settings = get_settings()
setup_logging(debug=_settings.app_debug)

log = logging.getLogger("api.main")


# ── Lifespan (startup / shutdown) ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Gestor del ciclo de vida de la aplicación FastAPI.

    Startup (antes del `yield`):
        Ejecuta una prueba de conectividad a MariaDB (`SELECT 1`). El fallo se
        registra como WARNING, no como ERROR crítico: la aplicación arranca
        igualmente aunque la base de datos no esté disponible en ese instante.
        Esto permite que el proceso levante en entornos donde el contenedor de
        BD tarda más en estar listo que el de la API (p.ej. docker-compose sin
        `healthcheck`). Todas las requests subsiguientes que requieran BD
        fallarán, pero el proceso no termina.

        Los imports de `engine` y `get_mongo_client` son lazys (dentro de la
        función) para evitar que el módulo `core.database` se importe en el
        momento de cargar `main.py`, lo que inicializaría el pool de conexiones
        antes de que el logging esté completamente configurado.

    Shutdown (después del `yield`):
        Cierra el cliente MongoDB explícitamente para liberar las conexiones
        del pool. El fallo se silencia (`except Exception: pass`) porque en
        el shutdown cualquier error de cierre es irrelevante. El engine de
        SQLAlchemy no se cierra explícitamente; SQLAlchemy gestiona el pool
        internamente al terminar el proceso.
    """
    _banner = (
        "\n"
        "====================================================\n"
        f"  {PROJECT_NAME}\n"
        f"  Version : {PROJECT_VERSION}\n"
        f"  Autor   : {PROJECT_AUTHOR}\n"
        f"  Empresa : {PROJECT_COMPANY}\n"
        "====================================================\n"
    )
    log.info(_banner)
    log.info("Arrancando %s [debug=%s]", PROJECT_NAME, _settings.app_debug)

    try:
        from core.database import engine
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        log.info("[DB] Conexión a MariaDB OK")
    except Exception as exc:
        log.warning("[DB] No se pudo conectar a MariaDB al inicio: %s", exc)

    yield

    try:
        from core.mongo import get_mongo_client
        get_mongo_client().close()
        log.info("[MongoDB] Conexión cerrada")
    except Exception:
        pass

    log.info("API detenida")


# ── Aplicación ────────────────────────────────────────────────────────────────

app = FastAPI(
    title=PROJECT_NAME,
    description=PROJECT_DESCRIPTION,
    version=PROJECT_VERSION,
    contact={
        "name": PROJECT_AUTHOR,
        "email": PROJECT_AUTHOR_EMAIL,
        "url": PROJECT_URL,
    },
    license_info={
        "name": PROJECT_LICENSE,
    },
    lifespan=lifespan,
)

# ── Middleware de logging HTTP ────────────────────────────────────────────────
# Se añade ANTES de CORS para medir el tiempo total de la request incluyendo
# el procesamiento de preflight.
# El import se hace aquí (tras crear `app`) en lugar de en el bloque de imports
# de la cabecera; de ahí el `# noqa: E402` para silenciar el aviso de PEP8.

from core.app_metadata_middleware import AppMetadataMiddleware  # noqa: E402
from core.debug_middleware import DebugLoggingMiddleware  # noqa: E402

app.add_middleware(DebugLoggingMiddleware)

# ── Middleware de metadatos del proyecto ──────────────────────────────────────
# Añade cabeceras X-App-* a todas las respuestas. Se registra ANTES de
# CORSMiddleware para que, cuando CORSMiddleware procesa la respuesta saliente,
# las cabeceras ya estén presentes y queden cubiertas por `expose_headers`.
app.add_middleware(AppMetadataMiddleware)

# ── CORS ──────────────────────────────────────────────────────────────────────
# allow_credentials=True es incompatible con allow_origins=["*"] (spec CORS).
# El wildcard sólo se admite sin credenciales. Con orígenes explícitos se activan
# las credenciales para que los clientes web puedan enviar el header Authorization.
#
# expose_headers lista las cabeceras X-App-* para que los clientes browser
# puedan leerlas mediante JavaScript (Access-Control-Expose-Headers).

_origins = _settings.cors_origins
_credentials = _origins != ["*"]

_expose_headers = [
    "X-App-Name",
    "X-App-Version",
    "X-App-Author",
    "X-App-Description",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=_expose_headers,
)

# ── Manejadores de excepción globales ────────────────────────────────────────

register_exception_handlers(app)

# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(info.router)
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(servidor.router)
app.include_router(servicio.router)
app.include_router(seccion.router)
app.include_router(grupo.router)
app.include_router(grupo_permisos.router)
app.include_router(permiso.router)
app.include_router(ambito.router)
app.include_router(usuario.router)


# ── Punto de entrada directo ──────────────────────────────────────────────────
# Solo para desarrollo local (`python main.py`). En producción se arranca con
# `uvicorn main:app` (sin reload) gestionado por el supervisor o el contenedor.

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)
