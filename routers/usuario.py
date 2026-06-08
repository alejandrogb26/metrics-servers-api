"""
Router HTTP para operaciones de autogestión del usuario autenticado.

Capa arquitectónica: Presentación / Routing HTTP.

Responsabilidades:
    - Exponer el endpoint que permite al usuario autenticado gestionar sus
      propios datos de aplicación (actualmente: foto de perfil).
    - Identificar al usuario por el JWT recibido, sin requerir un permiso
      específico más allá de la autenticación válida.

Qué NO debe contener este fichero:
    - Gestión de otros usuarios ni consultas de perfil ajeno.
    - Operaciones CRUD sobre la tabla `usuarios_app` en general. Este router
      es exclusivamente de autogestión.
    - Autenticación (login/logout). Eso pertenece a `routers/auth.py`.
    - Lectura de atributos LDAP. Los datos del directorio se leen en el
      flujo de login y se incluyen en el JWT / sesión.

Contrato HTTP de este router:

    ┌────────────────────────┬───────────────────────┬──────────────────────────────┐
    │ Método + Ruta          │ Autenticación         │ Respuesta exitosa            │
    ├────────────────────────┼───────────────────────┼──────────────────────────────┤
    │ POST /usuario/foto     │ Cualquier JWT válido  │ 200 UploadResult             │
    └────────────────────────┴───────────────────────┴──────────────────────────────┘

    A diferencia del resto de routers del proyecto, este no usa
    `require_permission(nombre)` sino `get_current_user` directamente.
    Esto es intencional: cualquier usuario autenticado puede subir su propia
    foto de perfil sin necesitar un permiso específico de dominio.

Relaciones con otros módulos:
    - `core/database.py`           → `get_session` proporciona la `Session` de BD.
    - `core/dependencies.py`       → `get_current_user` y `RequestUser` para
                                     identificar al usuario autenticado.
    - `models/common.py`           → `UploadResult` como esquema de respuesta.
    - `services/usuario_service.py`→ delega la subida a MinIO y la persistencia
                                     del nombre del fichero en BD.
    - `main.py`                    → registra este router con `app.include_router`.

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

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlmodel import Session

from core.database import get_session
from core.dependencies import RequestUser, get_current_user
from models.common import UploadResult
from services.usuario_service import UsuarioService

router = APIRouter(prefix="/usuario", tags=["Usuarios"])


@router.post("/foto", response_model=UploadResult)
async def upload_foto(
    # Fichero enviado por el cliente mediante multipart/form-data.
    # El campo del formulario debe llamarse "file".
    # FastAPI lo convierte automáticamente en un objeto UploadFile.
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    current_user: Annotated[RequestUser, Depends(get_current_user)] = None,  # type: ignore[assignment]
):
    """
    Sube o reemplaza la foto de perfil del usuario autenticado.
    El usuario se identifica por el JWT.

    Endpoint de autogestión: el usuario solo puede modificar su propia foto
    de perfil. La identidad del propietario se extrae de `current_user.username`,
    derivado del claim `sub` del JWT, sin que el cliente pueda especificar un
    usuario destino diferente.

    La dependencia usada es `get_current_user` (autenticación básica), no
    `require_permission(...)`. Esto significa que cualquier usuario con un JWT
    válido puede llamar a este endpoint, independientemente de sus permisos de
    dominio (`AUDIT_*` / `MODIFY_*`).

    Flujo de la operación:
        1. Valida que se recibe un fichero con nombre (`file.filename`). Si falta,
           devuelve `HTTP 400`.
        2. Valida que `current_user.username` no está vacío. Esta comprobación
           es defensiva: si `get_current_user` devuelve un `RequestUser` válido,
           `username` siempre está presente.
        3. Lee el contenido completo del fichero en memoria (`await file.read()`).
        4. Delega en `UsuarioService.update_foto_perfil`, que sube el fichero a
           MinIO y persiste el nombre resultante en `usuarios_app`.
        5. Devuelve `UploadResult` con `nombre_archivo` y `url_foto`.

    Es `async def` porque requiere `await file.read()`.

    A diferencia de `routers/servidor.py` (`upload_foto`), este endpoint sí
    devuelve `url_foto` en el `UploadResult`, lo que permite al cliente mostrar
    la nueva foto inmediatamente sin una petición GET adicional.

    Args:
        file:         Fichero a subir (multipart/form-data). Campo requerido.
        session:      Sesión de BD inyectada por `get_session`.
        current_user: Usuario autenticado extraído del JWT. Se usa para obtener
                      el `username` que identifica al propietario de la foto.

    Retorna:
        `UploadResult` con `nombre_archivo` (nombre del fichero en MinIO) y
        `url_foto` (URL pública de la foto de perfil).

    Errores HTTP:
        400 Bad Request  — fichero ausente o sin nombre.
        401 Unauthorized — token ausente, inválido, o `username` vacío en el
                           payload del JWT.
    """
    if not file or not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Archivo no proporcionado",
        )

    username = current_user.username
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token no válido",
        )

    data = await file.read()
    svc = UsuarioService(session)
    nombre, url_foto = svc.update_foto_perfil(username, data, file.filename)

    return UploadResult(nombre_archivo=nombre, url_foto=url_foto)
