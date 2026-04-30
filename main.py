"""
metrics-servers — API de monitorización de servidores
Python 3.12+ · FastAPI · SQLModel · MariaDB · MongoDB · MinIO · LDAP
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import get_settings
from exceptions.handlers import register_exception_handlers
from routers import (
    ambito,
    auth,
    grupo,
    grupo_permisos,
    health,
    permiso,
    seccion,
    servidor,
    servicio,
    usuario,
)


# ── Lifespan (startup / shutdown) ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: verificar conexión a BD (opcional, no bloquea si falla)
    try:
        from core.database import engine
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("[DB] Conexión a MariaDB OK")
    except Exception as exc:
        print(f"[DB] Advertencia: no se pudo conectar a MariaDB al inicio: {exc}")

    yield  # aquí corre la aplicación

    # Shutdown: cerrar cliente MongoDB
    try:
        from core.mongo import get_mongo_client
        get_mongo_client().close()
        print("[MongoDB] Conexión cerrada")
    except Exception:
        pass


# ── Aplicación ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Metrics Servers API",
    description="API REST de monitorización de servidores. Autenticación LDAP/AD + JWT.",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
# allow_credentials=True es incompatible con allow_origins=["*"] (spec CORS).
# El wildcard sólo se admite sin credenciales. Con orígenes explícitos se activan
# las credenciales para que los clientes web puedan enviar el header Authorization.

_settings = get_settings()
_origins = _settings.cors_origins
_credentials = _origins != ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Manejadores de excepción globales ────────────────────────────────────────

register_exception_handlers(app)

# ── Routers ───────────────────────────────────────────────────────────────────

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)
