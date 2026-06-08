"""
Router HTTP de diagnóstico de disponibilidad del servicio.

Capa arquitectónica: Presentación / Routing HTTP (infraestructura / observabilidad).

Responsabilidades:
    - Exponer un endpoint público (`GET /health/status`) que permite a sistemas
      externos (balanceadores de carga, plataformas de orquestación, monitorización)
      verificar que la API está activa y puede alcanzar su base de datos relacional.
    - Registrar en el log los errores de conectividad con la BD para facilitar
      el diagnóstico de incidencias.

Qué NO debe contener este fichero:
    - Comprobación de otros servicios de infraestructura (Redis, MongoDB, MinIO,
      LDAP). Este endpoint solo verifica MariaDB mediante `SELECT 1`.
    - Lógica de negocio ni autenticación. El endpoint es completamente público.

Contrato HTTP de este router:

    ┌────────────────────────┬───────────────┬────────────────────────────────────────┐
    │ Método + Ruta          │ Autenticación │ Respuesta                              │
    ├────────────────────────┼───────────────┼────────────────────────────────────────┤
    │ GET /health/status     │ Ninguna       │ 200 {"status":"OK","db":"up"}          │
    │                        │               │ 200 {"status":"ERROR"} (si BD caída)   │
    └────────────────────────┴───────────────┴────────────────────────────────────────┘

    Nota: el endpoint siempre devuelve HTTP 200, incluso cuando la BD no es
    accesible. El estado real se comunica a través del campo `status` del body.

Relaciones con otros módulos:
    - `core/database.py` → `get_session` proporciona la `Session` para emitir
                           el `SELECT 1` de comprobación.
    - `main.py`          → registra este router con `app.include_router`.

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

from fastapi import APIRouter, Depends
from sqlmodel import Session, text

from core.database import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("/status")
def health_status(session: Session = Depends(get_session)):
    """
    Endpoint público de diagnóstico. No requiere autenticación.

    Verifica la disponibilidad de la API y de su base de datos relacional
    (MariaDB) mediante una consulta mínima `SELECT 1`. Si la query tiene
    éxito, la BD está accesible. Si lanza una excepción, la BD está caída
    o la conexión no se puede establecer.

    Alcance de la comprobación:
        Solo verifica MariaDB. Redis (blocklist de tokens), MongoDB (métricas),
        MinIO (ficheros) y LDAP (autenticación) no se comprueban. Un resultado
        `{"status": "OK"}` garantiza únicamente que la capa de datos relacional
        responde, no que el sistema completo es funcional.

    Comportamiento en error:
        Si la query falla, registra el error completo con stack trace en el log
        (`logger.error(..., exc_info=True)`) y devuelve `{"status": "ERROR"}`.
        La excepción no se re-lanza: el endpoint siempre devuelve HTTP 200 para
        que la ruta sea alcanzable aunque la BD esté caída. El estado real de
        la BD se comunica a través del campo `status` del body, no del código
        HTTP.

    No declara `response_model`: la forma del body varía según el resultado
    (la respuesta de error omite el campo `db` presente en la respuesta de éxito).

    Args:
        session: Sesión de BD inyectada por `get_session`, usada para emitir
                 la consulta de comprobación.

    Retorna:
        `{"status": "OK", "db": "up"}` si MariaDB responde correctamente.
        `{"status": "ERROR"}` si se produce cualquier excepción al conectar.
        Código HTTP siempre 200.
    """
    try:
        session.exec(text("SELECT 1")).first()  # type: ignore[call-overload]
        return {"status": "OK", "db": "up"}
    except Exception as exc:
        logger.error("Health check DB error: %s", exc, exc_info=True)
        return {"status": "ERROR"}
