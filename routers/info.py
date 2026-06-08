"""
Router HTTP para el endpoint informativo público de la API.

Capa arquitectónica: Presentación / Routing HTTP.

Responsabilidades:
    - Exponer el endpoint `GET /info` que devuelve los metadatos de identificación
      del proyecto en formato JSON.
    - Ser un endpoint completamente público (sin autenticación) para que cualquier
      cliente o herramienta pueda descubrir nombre, versión, autor y licencia de
      la API sin necesidad de credenciales.

Qué NO debe contener este fichero:
    - Lógica de negocio ni acceso a datos.
    - Datos sensibles: emails privados, rutas internas, tokens ni configuración
      de infraestructura.

Autor:
    Alejandro Gómez Blanco

Proyecto:
    Metrics Servers

Versión:
    1.0.0

Organización:
    Metrics Servers Project
"""

from fastapi import APIRouter
from pydantic import BaseModel

from core.project_info import (
    PROJECT_AUTHOR,
    PROJECT_COMPANY,
    PROJECT_CREATED_AT,
    PROJECT_DESCRIPTION,
    PROJECT_LICENSE,
    PROJECT_NAME,
    PROJECT_URL,
    PROJECT_VERSION,
)

router = APIRouter(tags=["Info"])


class ProjectInfo(BaseModel):
    """Metadatos de identificación del proyecto devueltos por GET /info."""

    name: str
    version: str
    author: str
    company: str
    description: str
    license: str
    url: str
    created_at: str


# Instancia pre-construida: los valores son constantes, no hay necesidad de
# reconstruir el objeto en cada petición.
_PROJECT_INFO = ProjectInfo(
    name=PROJECT_NAME,
    version=PROJECT_VERSION,
    author=PROJECT_AUTHOR,
    company=PROJECT_COMPANY,
    description=PROJECT_DESCRIPTION,
    license=PROJECT_LICENSE,
    url=PROJECT_URL,
    created_at=PROJECT_CREATED_AT,
)


@router.get(
    "/info",
    response_model=ProjectInfo,
    summary="Información del proyecto",
    description=(
        "Devuelve los metadatos de identificación de la API: nombre, versión, "
        "autor, organización, descripción, licencia y URL del proyecto. "
        "Endpoint público, no requiere autenticación."
    ),
)
def get_info() -> ProjectInfo:
    """Devuelve los metadatos públicos del proyecto."""
    return _PROJECT_INFO
