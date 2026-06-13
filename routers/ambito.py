"""
Router HTTP para el recurso Ámbito.

Capa arquitectónica: Presentación / Routing HTTP.

Responsabilidades:
    - Definir los endpoints REST del recurso `/ambitos`.
    - Validar que el usuario autenticado posee el permiso requerido antes de
      invocar la capa de servicio.
    - Delegar toda la lógica de negocio en `AmbitoService`.
    - Transformar los resultados del servicio en respuestas HTTP con el código
      de estado y el esquema Pydantic adecuados.

Qué NO debe contener este fichero:
    - Lógica de negocio ni reglas de dominio.
    - Acceso directo a la base de datos (solo recibe `Session` vía `Depends`).
    - Gestión de permisos ni decodificación de tokens. Eso pertenece a
      `core/dependencies.py`.

Contrato HTTP de este router:

    ┌────────────────────────┬────────────────────┬────────────────────────────┐
    │ Método + Ruta          │ Permiso requerido  │ Respuesta exitosa          │
    ├────────────────────────┼────────────────────┼────────────────────────────┤
    │ GET /ambitos           │ AUDIT_SYS          │ 200 PagedResponse[AmbitoRead] │
    │ GET /ambitos/{id}      │ AUDIT_SYS          │ 200 AmbitoRead             │
    └────────────────────────┴────────────────────┴────────────────────────────┘

    Los ámbitos son datos de catálogo de solo lectura. No existen endpoints de
    escritura (POST, PUT, DELETE) porque los ámbitos se configuran en el
    despliegue del sistema y no se modifican a través de la API.

Relaciones con otros módulos:
    - `core/database.py`         → `get_session` proporciona la `Session` de BD.
    - `core/dependencies.py`     → `require_permission` y `RequestUser` para la
                                   guarda de autorización en cada endpoint.
    - `models/ambito.py`         → `AmbitoRead` como esquema de respuesta.
    - `models/common.py`         → `PagedResponse[AmbitoRead]` para la lista paginada.
    - `services/ambito_service.py` → delega la lógica de consulta y paginación.
    - `main.py`                  → registra este router con `app.include_router`.

Autor:
    Alejandro Gómez Blanco

Proyecto:
    Metrics Servers

Versión:
    1.0.0

Organización:
    Metrics Servers Project
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from core.database import get_session
from core.dependencies import RequestUser, require_permission
from models.ambito import AmbitoRead
from models.common import PagedResponse
from services.ambito_service import AmbitoService

# Router registrado en main.py bajo el prefijo /ambitos.
# El tag "Ámbitos" agrupa estos endpoints en la documentación OpenAPI/Swagger.
router = APIRouter(prefix="/ambitos", tags=["Ámbitos"])


@router.get("", response_model=PagedResponse[AmbitoRead])
def get_all(
    page: int = Query(default=0, ge=0, description="Página (base 0)"),
    size: int = Query(default=50, ge=1, le=100, description="Elementos por página"),
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("AUDIT_SYS"))] = None,  # type: ignore[assignment]
):
    """
    Devuelve una página de ámbitos disponibles en el sistema.

    Requiere el permiso `AUDIT_SYS`. Solo los usuarios con permisos de auditoría
    de configuración del sistema pueden consultar los ámbitos.

    El parámetro `_user` no se usa en el cuerpo de la función: su único propósito
    es activar la dependencia `require_permission("AUDIT_SYS")`, que eleva un
    `HTTPException(403)` si el usuario carece del permiso. El prefijo `_` indica
    que es un parámetro solo de efecto lateral (guarda de autorización). El valor
    por defecto `= None` y el `# type: ignore[assignment]` son necesarios para que
    FastAPI registre la dependencia correctamente sin que el verificador de tipos
    se queje.

    La paginación se valida en la capa HTTP: `page >= 0`, `1 <= size <= 100`.
    El `PagedResponse` se construye directamente con los valores ya validados por
    `Query`, sin pasar por `PagedResponse.of()` (que también aplicaría el clamp).

    Args:
        page:    Número de página, base 0. Por defecto 0 (primera página).
        size:    Número de elementos por página. Mínimo 1, máximo 100. Por defecto 50.
        session: Sesión de base de datos inyectada por `get_session`.
        _user:   Dependencia de autorización. No se usa en el cuerpo.

    Retorna:
        `PagedResponse[AmbitoRead]` con los ámbitos de la página solicitada y los
        metadatos de paginación (`total`, `page`, `size`, `totalPages`, `hasNext`).

    Errores HTTP:
        401 Unauthorized — token ausente o inválido (gestionado por `get_current_user`).
        403 Forbidden    — usuario sin permiso `AUDIT_SYS`.
    """
    service = AmbitoService(session)
    items, total = service.get_all(page=page, size=size)
    return PagedResponse(data=items, page=page, size=size, total=total)


@router.get("/{ambito_id}", response_model=AmbitoRead)
def get_by_id(
    ambito_id: int,
    session: Session = Depends(get_session),
    _user: Annotated[RequestUser, Depends(require_permission("AUDIT_SYS"))] = None,  # type: ignore[assignment]
):
    """
    Devuelve un ámbito por su identificador único.

    Requiere el permiso `AUDIT_SYS`. Si el ámbito con el ID indicado no existe,
    devuelve `HTTP 404` con el mensaje "Ámbito no encontrado".

    Si el ámbito no existe, `AmbitoService.get_by_id` lanza `NotFoundException`,
    que el handler global de `exceptions/handlers.py` traduce a una respuesta
    HTTP 404 con el contrato `{"error": "NOT_FOUND", "message": "..."}`.


    Args:
        ambito_id: Clave primaria del ámbito a recuperar.
        session:   Sesión de base de datos inyectada por `get_session`.
        _user:     Dependencia de autorización. No se usa en el cuerpo.

    Retorna:
        `AmbitoRead` con el id, nombre y descripción del ámbito.

    Errores HTTP:
        401 Unauthorized — token ausente o inválido.
        403 Forbidden    — usuario sin permiso `AUDIT_SYS`.
        404 Not Found    — no existe un ámbito con `ambito_id`.
    """
    service = AmbitoService(session)
    return service.get_by_id(ambito_id)
